"""Declarative base, shared column mixins and type helpers for all ORM models."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names so Alembic migrations are stable and reviewable.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ENUM_COLUMN_LENGTH = 32


class Base(DeclarativeBase):
    """Base class for every ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy reads this class attribute
        dict[str, Any]: JSONB,
        list[str]: JSONB,
        datetime: DateTime(timezone=True),
    }


class UUIDPrimaryKeyMixin:
    """UUIDv7 primary key: globally unique, time-ordered (good index locality), safe in URLs."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)


class TimestampMixin:
    """Server-side creation timestamp."""

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


def str_enum(enum_cls: type[StrEnum]) -> SAEnum:
    """Map a `StrEnum` to a VARCHAR column storing the enum *values*.

    Non-native enums avoid Postgres `ALTER TYPE` migrations when a value is added; validation
    happens in Python.

    Args:
        enum_cls: The enum class to store.

    Returns:
        A SQLAlchemy `Enum` type.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=ENUM_COLUMN_LENGTH,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )
