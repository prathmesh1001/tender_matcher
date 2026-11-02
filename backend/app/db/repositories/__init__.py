"""Repositories: the only modules allowed to query the database."""

from app.db.repositories.chunk_repository import ChunkRepository
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.embedding_repository import EmbeddingRepository
from app.db.repositories.judge_answer_repository import JudgeAnswerRepository
from app.db.repositories.result_repository import ResultRepository
from app.db.repositories.run_repository import RunRepository
from app.db.repositories.section_repository import SectionRepository
from app.db.repositories.spec_fact_repository import SpecFactRepository
from app.db.repositories.tender_repository import TenderRepository

__all__ = [
    "ChunkRepository",
    "DocumentRepository",
    "EmbeddingRepository",
    "JudgeAnswerRepository",
    "ResultRepository",
    "RunRepository",
    "SectionRepository",
    "SpecFactRepository",
    "TenderRepository",
]
