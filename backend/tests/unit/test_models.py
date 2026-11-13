"""Model metadata checks that need no database."""

import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKeyConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers

from app.db.models import Base, Document, MatchResult, Run
from app.db.models.base import str_enum
from app.schemas.enums import MatchStatus, Operator, RunStatus

EXPECTED_TABLES = {
    "documents",
    "pages",
    "chunks",
    "document_sections",
    "spec_facts",
    "tenders",
    "tender_documents",
    "text_embeddings",
    "requirements",
    "runs",
    "results",
    "evidence",
    "line_items",
    "judge_answers",
    "file_blobs",
    "stored_files",
}


def test_all_expected_tables_are_registered() -> None:
    configure_mappers()
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_str_enum_stores_values_not_member_names() -> None:
    column_type = str_enum(Operator)
    assert isinstance(column_type, SAEnum)
    assert ">=" in column_type.enums
    assert "GTE" not in column_type.enums
    assert column_type.native_enum is False


def test_enum_bind_processor_emits_value() -> None:
    column_type = Run.__table__.c.status.type
    process = column_type.bind_processor(postgresql.dialect())  # type: ignore[no-untyped-call]
    assert process is not None
    assert process(RunStatus.QUEUED) == "queued"


def test_primary_key_default_is_uuid7() -> None:
    default = Document.__table__.c.id.default
    assert default is not None
    generated = default.arg(None)
    assert isinstance(generated, uuid.UUID)
    assert generated.version == 7


def test_every_foreign_key_declares_on_delete() -> None:
    missing = [
        f"{table.name}.{fk.column_keys}"
        for table in Base.metadata.tables.values()
        for fk in table.constraints
        if isinstance(fk, ForeignKeyConstraint) and fk.ondelete is None
    ]
    assert missing == []


def test_score_is_nullable_so_unsupported_can_be_excluded() -> None:
    assert MatchResult.__table__.c.score.nullable is True
    assert MatchStatus.UNSUPPORTED.value == "unsupported"
