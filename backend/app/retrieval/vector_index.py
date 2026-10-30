"""Write chunk vectors to pgvector, embedding each distinct text once per model."""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.exceptions import ExternalServiceError
from app.db.models import TextEmbedding
from app.db.models.embedding import EMBEDDING_DIMENSIONS
from app.db.repositories import ChunkRepository, EmbeddingRepository
from app.retrieval.embedder import Embedder, embedding_text, text_hash


@dataclass(frozen=True, slots=True)
class EmbedStats:
    """What embedding one document did."""

    chunks: int
    distinct_texts: int
    embedded: int

    @property
    def reused(self) -> int:
        """Distinct texts whose vector already existed (other documents, earlier runs)."""
        return self.distinct_texts - self.embedded


def embed_document_chunks(
    session: Session, embedder: Embedder, document_id: uuid.UUID
) -> EmbedStats:
    """Give every chunk of a document a vector, embedding only texts not stored yet.

    Each chunk gets `embed_hash = sha256(embedding_text(raw_text))`; vectors are stored once
    per (model, hash) in `text_embeddings`.

    Args:
        session: Open DB session (the caller commits; one document per transaction).
        embedder: The embedding model.
        document_id: Document whose chunks to embed.

    Returns:
        Counts for logging.

    Raises:
        ExternalServiceError: If the model fails or returns vectors of the wrong size.
    """
    texts: dict[str, str] = {}
    chunks = ChunkRepository(session).list_for_document(document_id)
    for chunk in chunks:
        text = embedding_text(chunk.raw_text)
        chunk.embed_hash = text_hash(text)
        texts.setdefault(chunk.embed_hash, text)
    repo = EmbeddingRepository(session)
    stored = repo.existing_hashes(embedder.model_name, texts.keys())
    missing = [h for h in texts if h not in stored]
    vectors = embedder.embed([texts[h] for h in missing])
    wrong = {len(v) for v in vectors} - {EMBEDDING_DIMENSIONS}
    if wrong:
        raise ExternalServiceError(
            f"{embedder.model_name} returned {sorted(wrong)}-d vectors, "
            f"expected {EMBEDDING_DIMENSIONS}"
        )
    repo.add_all(
        [
            TextEmbedding(model=embedder.model_name, text_hash=h, embedding=v)
            for h, v in zip(missing, vectors, strict=True)
        ]
    )
    return EmbedStats(chunks=len(chunks), distinct_texts=len(texts), embedded=len(missing))
