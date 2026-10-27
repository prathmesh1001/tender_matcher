"""ORM models. Importing this package registers every table on `Base.metadata`."""

from app.db.models.base import Base
from app.db.models.chunk import Chunk
from app.db.models.document import Document, Page
from app.db.models.result import Evidence, MatchResult
from app.db.models.run import Run
from app.db.models.spec_fact import SpecFact
from app.db.models.tender import Requirement, Tender

__all__ = [
    "Base",
    "Chunk",
    "Document",
    "Evidence",
    "MatchResult",
    "Page",
    "Requirement",
    "Run",
    "SpecFact",
    "Tender",
]
