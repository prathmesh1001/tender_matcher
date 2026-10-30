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
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy.orm import Session

from app.core.logging import log_context
from app.db.models import Chunk, Document, DocumentSection, Page
from app.db.repositories import ChunkRepository, DocumentRepository, SectionRepository
from app.ingestion.processing import (
    ProcessedDocument,
    ProcessingOptions,
    process_file,
    set_product_family,
    tag_inputs,
    tag_products,
)
from app.ingestion.product_tagger import Declared, tag_chunks
from app.ingestion.registry import verify_source_file
from app.ingestion.sections import build_sections, classify_chunks
from app.retrieval.text_normalizer import normalize_for_search
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import DocumentSource
from app.schemas.sources import RegisteredSource
from app.storage.file_store import FileStore

logger = logging.getLogger(__name__)


class IngestStatus(StrEnum):
    """What happened to one file."""

    INGESTED = "ingested"
    REPROCESSED = "reprocessed"
    SKIPPED_EXISTING = "skipped_existing"
    SKIPPED_UNSUPPORTED = "skipped_unsupported"


@dataclass(slots=True)
class ReprocessPolicy:
    """Whether stored documents are re-parsed, remembering which were already done this run.

    A file shared by several tenders is reprocessed once per run, not once per tender.
    """

    enabled: bool = False
    done: set[str] = field(default_factory=set)

    def claim(self, sha256: str) -> bool:
        """True if this stored document should be reprocessed now (and mark it as done)."""
        if not self.enabled or sha256 in self.done:
            return False
        self.done.add(sha256)
        return True


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """Result of ingesting one file."""

    file_ref: str
    status: IngestStatus
    document_id: uuid.UUID | None = None
    pages: int = 0
    chunks: int = 0


def _chunk_row(page: Page, draft: ChunkDraft) -> Chunk:
    """ORM row for one chunk draft (the caller appends it to `document.chunks`)."""
    return Chunk(
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
        product_type=draft.product_type,
        product_sub_type=draft.product_sub_type,
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
    # Append on the parent side: SQLAlchemy 2.0 does not cascade a child into the session
    # through its back-reference when the parent is already persistent (reprocessing).
    for draft in processed.chunks:
        document.chunks.append(_chunk_row(pages[draft.page_number], draft))
    document.page_count = len(processed.pages)


def replace_content(session: Session, document: Document, processed: ProcessedDocument) -> None:
    """Swap a stored document's pages and chunks for freshly processed ones, in place.

    The document row (and so its id, tender links and provenance) is kept; spec facts that
    pointed at old chunks lose that link (FK set null) and are re-extracted later.

    Args:
        session: Open DB session.
        document: A stored document.
        processed: Its newly processed content.
    """
    ChunkRepository(session).delete_for_document(document.id)
    DocumentRepository(session).delete_pages(document.id)
    session.refresh(document, ["pages", "chunks"])
    attach_content(document, processed)
    session.flush()


def rebuild_sections(session: Session, document: Document, options: ProcessingOptions) -> int:
    """(Re)build a stored document's sections and its chunks' categories (no re-parsing).

    Called after every ingest and by the `classify-sections` backfill. Old sections are
    removed first, so it is idempotent.

    Args:
        session: Open DB session.
        document: A stored (flushed) document.
        options: Processing options (section classifier and vocabulary).

    Returns:
        Number of sections stored.
    """
    repo = SectionRepository(session)
    repo.delete_for_document(document.id)
    chunks = ChunkRepository(session).list_for_document(document.id)
    classifier = options.section_classifier
    categories = classify_chunks(
        [c.section for c in chunks], [c.search_text for c in chunks], classifier, options.vocabulary
    )
    drafts = build_sections(
        [c.section for c in chunks], [c.page_number for c in chunks], categories, classifier.default
    )
    rows: list[DocumentSection] = []
    for draft in drafts:
        parent = rows[draft.parent_position] if draft.parent_position is not None else None
        rows.append(
            DocumentSection(
                document_id=document.id,
                parent=parent,
                position=draft.position,
                title=draft.title,
                number=draft.number,
                category=draft.category,
                category_source=draft.category_source,
                start_page=draft.start_page,
                end_page=draft.end_page,
                chunk_count=len(draft.chunk_indexes),
            )
        )
    repo.add_all(rows)
    for draft, row in zip(drafts, rows, strict=True):
        for i in draft.chunk_indexes:
            chunks[i].section_id = row.id
            chunks[i].section_category = categories[i].category
    session.flush()
    return len(rows)


def declared_by(source: RegisteredSource) -> Declared:
    """Product types and sub-types a catalogue declares in sources.yaml."""
    return Declared(tuple(source.product_types), tuple(source.sub_types))


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
        product_types=list(source.product_types),
        sub_types=list(source.sub_types),
        sha256=source.sha256,
        fetched_at=source.fetched_at,
        publish_date=source.publish_date,
    )


def ingest_manufacturer_source(
    session: Session,
    store: FileStore,
    source: RegisteredSource,
    options: ProcessingOptions,
    reprocess: ReprocessPolicy | None = None,
) -> IngestOutcome:
    """Ingest one registered catalogue, unless it is already stored.

    Args:
        session: Open DB session (the caller commits).
        store: File store holding the catalogue.
        source: The registry entry.
        options: Processing options.
        reprocess: If enabled, an already stored catalogue is re-parsed in place.

    Returns:
        What happened.

    Raises:
        SourceRegistryError: If the file is missing or its sha256 differs from sources.yaml.
        DocumentParseError: If the file cannot be parsed.
    """
    policy = reprocess or ReprocessPolicy()
    file_ref = store.ref_for(source.path)
    with log_context(manufacturer=source.manufacturer):
        repo = DocumentRepository(session)
        existing = repo.get_by_sha256(source.sha256)
        if existing is not None and not policy.claim(source.sha256):
            logger.info("document already ingested", extra={"file": file_ref})
            return IngestOutcome(file_ref, IngestStatus.SKIPPED_EXISTING, existing.id)
        verify_source_file(source)
        with store.local_path(file_ref) as path:
            processed = process_file(path, options)
        processed = tag_products(processed, options, declared_by(source))
        processed = set_product_family(processed, source.title)
        if existing is not None:
            existing.product_types = list(source.product_types)
            existing.sub_types = list(source.sub_types)
            replace_content(session, existing, processed)
            document, status = existing, IngestStatus.REPROCESSED
        else:
            document = _manufacturer_document(source, file_ref)
            attach_content(document, processed)
            repo.add(document)
            status = IngestStatus.INGESTED
        rebuild_sections(session, document, options)
        logger.info(
            "document stored",
            extra={"file": file_ref, "status": status.value, "chunks": len(processed.chunks)},
        )
        return IngestOutcome(
            file_ref,
            status,
            document.id,
            pages=len(processed.pages),
            chunks=len(processed.chunks),
        )


def retag_document(session: Session, document: Document, options: ProcessingOptions) -> int:
    """Re-run product tagging on a stored document's chunks (no re-parsing).

    Catalogue chunks also get their product family (the document title) refreshed.

    Args:
        session: Open DB session.
        document: A stored document (its declared product types are already up to date).
        options: Processing options (vocabulary and product matcher).

    Returns:
        Number of chunks whose tag changed.
    """
    chunks = ChunkRepository(session).list_for_document(document.id)
    items = tag_inputs(
        [c.section for c in chunks],
        [c.search_text for c in chunks],
        [c.page_number for c in chunks],
        [c.meta.get("section_page") for c in chunks],
        options.vocabulary,
        [c.chunk_type for c in chunks],
    )
    declared = Declared(tuple(document.product_types), tuple(document.sub_types))
    page_texts = {
        p.page_number: normalize_for_search(p.text, options.vocabulary) for p in document.pages
    }
    family = document.title if document.source_kind == DocumentSource.MANUFACTURER else None
    changed = 0
    for chunk, tag in zip(
        chunks,
        tag_chunks(
            items, options.product_matcher, declared, options.product_page_min_tagged, page_texts
        ),
        strict=True,
    ):
        new = (tag.product_type, tag.sub_type, family or chunk.product_family)
        if (chunk.product_type, chunk.product_sub_type, chunk.product_family) != new:
            chunk.product_type, chunk.product_sub_type, chunk.product_family = new
            changed += 1
    session.flush()
    return changed
