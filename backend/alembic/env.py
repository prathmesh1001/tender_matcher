"""Alembic environment.

The database URL comes from `app.core.config` (never from alembic.ini). Callers such as the test
suite may pass an open connection via `config.attributes["connection"]`.
"""

from alembic import context
from sqlalchemy import Connection, create_engine, pool

import app.db.models  # noqa: F401 - registers every table on Base.metadata
from app.core.config import get_settings
from app.db.models.base import Base

target_metadata = Base.metadata


def _configure_and_run(connection: Connection) -> None:
    """Run migrations on an open connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it (`alembic upgrade head --sql`)."""
    context.configure(
        url=str(get_settings().DATABASE_URL),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _configure_and_run(connection)
        return
    engine = create_engine(str(get_settings().DATABASE_URL), poolclass=pool.NullPool)
    with engine.connect() as conn:
        _configure_and_run(conn)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
