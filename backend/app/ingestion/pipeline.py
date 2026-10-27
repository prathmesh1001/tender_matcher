"""Manufacturer ingestion: registered catalogue -> verified file -> pages + chunks in Postgres.

Stages (each in its own module, independently tested):
registry (sources.yaml, sha256 check) -> processing (parse, OCR, tables, chunks, search_text)
-> repositories (documents, pages, chunks).

Idempotent: a document whose sha256 is already stored is skipped, so re-running never
duplicates data (CLAUDE.md section 6). Spec-fact extraction and indexing hook in after this
stage (plan steps 6-7).
"""

import logging
import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from app.core.logging import log_context
from app.db.models import Chunk, Document, Page
from app.db.repositories import DocumentRepository
from app.ingestion.processing import ProcessedDocument, ProcessingOptions, process_file
from app.ingestion.registry import verify_source_file
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import DocumentSource
from app.schemas.sources import RegisteredSource
from app.storage.file_store import FileStore

logger = logging.getLogger(__name__)


class IngestStatus(StrEnum):
    """What happened to one file."""

    INGESTED = "ingested"
    SKIPPED_EXISTING = "skipped_existing"
    SKIPPED_UNSUPPORTED = "skipped_unsupported"


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """Result of ingesting one file."""

    file_ref: str
    status: IngestStatus
    document_id: uuid.UUID | None = None
    pages: int = 0
    chunks: int = 0


def _chunk_row(document: Document, page: Page, draft: ChunkDraft) -> Chunk:
    """ORM row for one chunk draft (added to `document.chunks` through the relationship)."""
    return Chunk(
        document=document,
        page=page,
        page_number=draft.page_number,
        position=draft.position,
        chunk_type=draft.chunk_type,
        raw_text=draft.raw_text,
        search_text=draft.search_text,
        section=draft.section,
        product_family=draft.product_family,
        model=draft.model,
        token_count=draft.token_count,
        meta=draft.meta,
    )


def attach_content(document: Document, processed: ProcessedDocument) -> None:
    """Add pages and chunks to a new, unsaved document.

    Args:
        document: A new document.
        processed: Its processed pages and chunks.
    """
    pages: dict[int, Page] = {}
    for processed_page in processed.pages:
        page = Page(
            page_number=processed_page.page_number,
            text=processed_page.text,
            ocr=processed_page.ocr,
            char_count=processed_page.char_count,
        )
        pages[page.page_number] = page
        document.pages.append(page)
    for draft in processed.chunks:
        _chunk_row(document, pages[draft.page_number], draft)
    document.page_count = len(processed.pages)


def _manufacturer_document(source: RegisteredSource, file_ref: str) -> Document:
    """Unsaved document row for a registered catalogue."""
    return Document(
        source_kind=DocumentSource.MANUFACTURER,
        manufacturer=source.manufacturer,
        category=source.category,
        title=source.title,
        doc_type=source.doc_type,
        url=str(source.url),
        file_path=file_ref,
        sha256=source.sha256,
        fetched_at=source.fetched_at,
        publish_date=source.publish_date,
    )


def ingest_manufacturer_source(
    session: Session, store: FileStore, source: RegisteredSource, options: ProcessingOptions
) -> IngestOutcome:
    """Ingest one registered catalogue, unless it is already stored.

    Args:
        session: Open DB session (the caller commits).
        store: File store holding the catalogue.
        source: The registry entry.
        options: Processing options.

    Returns:
        What happened.

    Raises:
        SourceRegistryError: If the file is missing or its sha256 differs from sources.yaml.
        DocumentParseError: If the file cannot be parsed.
    """
    file_ref = store.ref_for(source.path)
    with log_context(manufacturer=source.manufacturer):
        repo = DocumentRepository(session)
        existing = repo.get_by_sha256(source.sha256)
        if existing is not None:
            logger.info("document already ingested", extra={"file": file_ref})
            return IngestOutcome(file_ref, IngestStatus.SKIPPED_EXISTING, existing.id)
        verify_source_file(source)
        with store.local_path(file_ref) as path:
            processed = process_file(path, options)
        document = _manufacturer_document(source, file_ref)
        attach_content(document, processed)
        repo.add(document)
        logger.info(
            "document ingested",
            extra={
                "file": file_ref,
                "pages": len(processed.pages),
                "chunks": len(processed.chunks),
            },
        )
        return IngestOutcome(
            file_ref,
            IngestStatus.INGESTED,
            document.id,
            pages=len(processed.pages),
            chunks=len(processed.chunks),
        )
