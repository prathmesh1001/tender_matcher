from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, inspect

from app.db.models import Base
from tests.integration.conftest import alembic_config


def _user_tables(conn: Connection) -> set[str]:
    return set(inspect(conn).get_table_names()) - {"alembic_version"}


def test_migrations_create_every_model_table(db_connection: Connection) -> None:
    assert _user_tables(db_connection) == set(Base.metadata.tables)


def test_models_and_migrations_are_in_sync(db_connection: Connection) -> None:
    context = MigrationContext.configure(
        db_connection, opts={"compare_type": True, "compare_server_default": True}
    )
    diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"Models changed without a migration: {diff}"


def test_downgrade_and_upgrade_round_trip(db_connection: Connection) -> None:
    # Postgres DDL is transactional, so the fixture's rollback restores the schema afterwards.
    config = alembic_config(db_connection)
    command.downgrade(config, "base")
    assert _user_tables(db_connection) == set()
    command.upgrade(config, "head")
    assert _user_tables(db_connection) == set(Base.metadata.tables)
