"""Indexing use cases: embed stored chunks into pgvector.

One document per transaction, so an interrupted run resumes where it stopped (documents whose
chunks all have vectors are not selected again).
"""

import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.exceptions import AppError
from app.db.repositories import ChunkRepository
from app.db.session import create_db_engine, create_session_factory, session_scope
from app.retrieval.embedder import Embedder, build_embedder
from app.retrieval.vector_index import EmbedStats, embed_document_chunks
from app.schemas.enums import DocumentSource
from app.services.ingestion_service import BatchResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EmbedOutcome:
    """Result of embedding one document."""

    document_id: uuid.UUID
    file_path: str
    stats: EmbedStats
    seconds: float


@dataclass(slots=True)
class IndexingService:
    """Embeds chunks; collaborators are injected."""

    session_factory: sessionmaker[Session]
    embedder: Embedder

    def embed_documents(
        self, *, source_kind: DocumentSource | None = None, manufacturer: str | None = None
    ) -> BatchResult[EmbedOutcome]:
        """Embed every document that still has chunks without a vector.

        Args:
            source_kind: Only manufacturer or only tender documents.
            manufacturer: Only this manufacturer.

        Returns:
            Per-document outcomes and failures.
        """
        with session_scope(self.session_factory) as session:
            todo = ChunkRepository(session).documents_needing_embedding(
                self.embedder.model_name, source_kind=source_kind, manufacturer=manufacturer
            )
        result: BatchResult[EmbedOutcome] = BatchResult()
        for document_id, file_path in todo:
            try:
                result.succeeded.append(self._embed_one(document_id, file_path))
            except (AppError, SQLAlchemyError) as exc:
                logger.error("embedding failed", extra={"file": file_path, "error": str(exc)})
                result.failed.append((file_path, str(exc)))
        return result

    def _embed_one(self, document_id: uuid.UUID, file_path: str) -> EmbedOutcome:
        """Embed one document in its own transaction."""
        started = time.perf_counter()
        with session_scope(self.session_factory) as session:
            stats = embed_document_chunks(session, self.embedder, document_id)
        seconds = time.perf_counter() - started
        logger.info("document embedded", extra={"file": file_path, "embedded": stats.embedded})
        return EmbedOutcome(document_id, file_path, stats, seconds)


def build_indexing_service(settings: Settings) -> IndexingService:
    """Wire the indexing service from settings."""
    return IndexingService(
        session_factory=create_session_factory(create_db_engine(settings)),
        embedder=build_embedder(settings),
    )
