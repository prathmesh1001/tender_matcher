"""Dense vectors, one per distinct embedded text and model (shared by identical chunks)."""

from typing import Final

from pgvector.sqlalchemy import Vector
from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# bge-m3 output size. Changing the embedding model to one with another size needs a migration.
EMBEDDING_DIMENSIONS: Final = 1024


class TextEmbedding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Vector of one embedded text. Chunks link to it through `Chunk.embed_hash`.

    Keyed by (model, text hash), so identical text in several documents, or a document that is
    re-parsed, is never embedded twice.
    """

    __tablename__ = "text_embeddings"
    __table_args__ = (
        UniqueConstraint("model", "text_hash"),
        Index(
            "ix_text_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    model: Mapped[str] = mapped_column(String(128))
    text_hash: Mapped[str] = mapped_column(String(64), comment="sha256 of the embedded text.")
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
