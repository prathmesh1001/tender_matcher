"""Fixtures for tests that need the real PostgreSQL database.

The schema is built with Alembic (not `create_all`), so every run also tests the migrations.
Each test runs inside a transaction that is rolled back, so tests never see each other's data.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from tests.conftest import get_test_database_url

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _ensure_database_exists(url: str) -> None:
    """Create the test database if missing (CREATE DATABASE cannot run in a transaction)."""
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            )
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        admin.dispose()


def alembic_config(connection: Connection) -> Config:
    """Alembic config that runs migrations on `connection` instead of DATABASE_URL."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["connection"] = connection
    return config


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    """Engine on a freshly migrated test database; skips DB tests if Postgres is down."""
    url = get_test_database_url()
    try:
        _ensure_database_exists(url)
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL not reachable at {make_url(url).render_as_string()}: {exc}")
    engine = create_engine(url)
    with engine.begin() as conn:
        config = alembic_config(conn)
        command.downgrade(config, "base")
        command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db_connection(db_engine: Engine) -> Iterator[Connection]:
    """A connection inside an outer transaction that is always rolled back."""
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


@pytest.fixture
def db_session(db_connection: Connection) -> Iterator[Session]:
    """Session whose commits become savepoints, so the outer rollback undoes everything."""
    session = Session(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        yield session
    finally:
        session.close()
