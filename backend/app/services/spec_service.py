"""Use case: extract verified spec facts from stored catalogues with the LLM.

One document per transaction, and the LLM is called outside any transaction. A document with
stored facts is skipped, so a re-run costs nothing and never duplicates; `reprocess` replaces
its facts. A document whose LLM call fails keeps what it had and is reported.
"""

import logging
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.exceptions import AppError, NotFoundError, OntologyError
from app.core.logging import log_context
from app.db.models import SpecFact
from app.db.repositories import ChunkRepository, DocumentRepository, SpecFactRepository
from app.db.session import create_db_engine, create_session_factory, session_scope
from app.ingestion.spec_extractor import ExtractionOptions, SourceChunk, extract_facts
from app.llm.base import LLMClient
from app.llm.factory import build_llm_client
from app.ontology.loader import load_ontologies
from app.ontology.schema import Ontology
from app.schemas.enums import DocumentSource
from app.schemas.spec_facts import DropReason, SpecFactDraft
from app.services.ingestion_service import BatchResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SpecOutcome:
    """Result of extracting one document."""

    document_id: uuid.UUID
    file_path: str
    proposed: int
    stored: int
    dropped: Counter[DropReason]


@dataclass(frozen=True, slots=True)
class _Source:
    """A stored document, read into plain data so no transaction spans the LLM calls."""

    document_id: uuid.UUID
    file_path: str
    manufacturer: str
    category: str
    chunks: list[SourceChunk]
    pages: dict[int, str]
    ocr_pages: frozenset[int]
    chunk_products: dict[uuid.UUID, tuple[str | None, str | None]]


@dataclass(slots=True)
class SpecExtractionService:
    """Extracts spec facts; collaborators are injected."""

    session_factory: sessionmaker[Session]
    llm: LLMClient
    ontologies: Mapping[str, Ontology]
    options: ExtractionOptions

    def extract(
        self, *, manufacturer: str | None = None, reprocess: bool = False
    ) -> BatchResult[SpecOutcome]:
        """Extract facts from every stored catalogue that has none yet.

        Args:
            manufacturer: Only this manufacturer.
            reprocess: Also replace the facts of documents that already have some.

        Returns:
            Per-document outcomes and failures.
        """
        result: BatchResult[SpecOutcome] = BatchResult()
        for document_id, file_path in self._todo(manufacturer, reprocess):
            try:
                result.succeeded.append(self._extract_one(document_id))
            except (AppError, SQLAlchemyError) as exc:
                logger.error("extraction failed", extra={"file": file_path, "error": str(exc)})
                result.failed.append((file_path, str(exc)))
        return result

    def _todo(self, manufacturer: str | None, reprocess: bool) -> list[tuple[uuid.UUID, str]]:
        """Catalogues to extract: all of them, or those without facts."""
        with session_scope(self.session_factory) as session:
            documents = DocumentRepository(session).find(
                manufacturer=manufacturer, source_kind=DocumentSource.MANUFACTURER
            )
            facts = SpecFactRepository(session)
            return [
                (d.id, d.file_path)
                for d in documents
                if reprocess or facts.count_for_document(d.id) == 0
            ]

    def _load(self, document_id: uuid.UUID) -> _Source:
        """Read one document's chunks and pages."""
        with session_scope(self.session_factory) as session:
            document = DocumentRepository(session).get(document_id)
            if document is None:
                raise NotFoundError("Document not found", details={"id": str(document_id)})
            chunks = ChunkRepository(session).list_for_document(document_id)
            return _Source(
                document_id=document_id,
                file_path=document.file_path,
                manufacturer=document.manufacturer or "",
                category=document.category or "",
                chunks=[
                    SourceChunk(
                        c.id, c.page_number, c.raw_text, c.section, c.product_family, c.model
                    )
                    for c in chunks
                ],
                pages={p.page_number: p.text for p in document.pages},
                ocr_pages=frozenset(p.page_number for p in document.pages if p.ocr),
                chunk_products={c.id: (c.product_type, c.product_sub_type) for c in chunks},
            )

    def _extract_one(self, document_id: uuid.UUID) -> SpecOutcome:
        """Extract one document and replace its facts in one transaction."""
        source = self._load(document_id)
        ontology = self.ontologies.get(source.category)
        if ontology is None:
            raise OntologyError(
                f"No ontology for category {source.category!r}",
                details={"category": source.category},
            )
        with log_context(manufacturer=source.manufacturer):
            result = extract_facts(self.llm, source.chunks, source.pages, ontology, self.options)
            with session_scope(self.session_factory) as session:
                repo = SpecFactRepository(session)
                repo.delete_for_document(document_id)
                repo.add_all(_rows(source, result.facts))
            logger.info(
                "spec facts stored",
                extra={
                    "file": source.file_path,
                    "proposed": result.proposed,
                    "stored": len(result.facts),
                },
            )
        return SpecOutcome(
            document_id, source.file_path, result.proposed, len(result.facts), result.dropped
        )


def _rows(source: _Source, facts: Sequence[SpecFactDraft]) -> list[SpecFact]:
    """ORM rows for one document's verified facts, tagged with their chunk's product."""
    rows = []
    for fact in facts:
        product_type, sub_type = (
            source.chunk_products.get(fact.chunk_id, (None, None))
            if fact.chunk_id is not None
            else (None, None)
        )
        rows.append(
            SpecFact(
                document_id=source.document_id,
                manufacturer=source.manufacturer,
                category=source.category,
                product_type=product_type,
                product_sub_type=sub_type,
                ocr=fact.page_number in source.ocr_pages,
                **fact.model_dump(exclude={"ocr"}),
            )
        )
    return rows


def build_spec_service(settings: Settings) -> SpecExtractionService:
    """Wire the extraction service from settings.

    Args:
        settings: Application settings.

    Returns:
        A ready service.

    Raises:
        InvalidInputError: If the LLM provider's API key is not configured.
    """
    return SpecExtractionService(
        session_factory=create_session_factory(create_db_engine(settings)),
        llm=build_llm_client(settings),
        ontologies={o.category: o for o in load_ontologies(settings.ONTOLOGY_DIR)},
        options=ExtractionOptions.from_settings(settings),
    )
