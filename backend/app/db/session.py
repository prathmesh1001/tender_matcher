"""Engine and session lifecycle.

Repositories never commit. A unit of work (`session_scope`, or the request-scoped dependency)
commits once on success and rolls back on any exception.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    """Create a pooled engine from settings. Does not open a connection yet.

    Args:
        settings: Application settings with the DB URL and pool options.

    Returns:
        A SQLAlchemy engine.
    """
    return create_engine(
        str(settings.DATABASE_URL),
        echo=settings.DB_ECHO,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT_S,
        pool_pre_ping=True,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to `engine`.

    `expire_on_commit=False` lets services return loaded objects after the commit.

    Args:
        engine: The engine sessions will use.

    Returns:
        A configured `sessionmaker`.
    """
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Provide a transactional unit of work.

    Args:
        factory: Session factory to open the session from.

    Yields:
        An open session. Committed on normal exit, rolled back and re-raised on error, always
        closed.
    """
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
