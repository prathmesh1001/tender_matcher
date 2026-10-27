from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.session import create_db_engine, create_session_factory, session_scope


def _factory_returning(session: MagicMock) -> sessionmaker[Session]:
    factory = MagicMock(spec=sessionmaker)
    factory.return_value = session
    return factory


def test_session_scope_commits_and_closes_on_success() -> None:
    session = MagicMock(spec=Session)
    with session_scope(_factory_returning(session)) as s:
        assert s is session
    session.commit.assert_called_once()
    session.rollback.assert_not_called()
    session.close.assert_called_once()


def test_session_scope_rolls_back_reraises_and_closes_on_error() -> None:
    session = MagicMock(spec=Session)
    with pytest.raises(RuntimeError, match="boom"), session_scope(_factory_returning(session)):
        raise RuntimeError("boom")
    session.commit.assert_not_called()
    session.rollback.assert_called_once()
    session.close.assert_called_once()


def test_engine_uses_settings_without_connecting(settings: Settings) -> None:
    custom = settings.model_copy(update={"DB_POOL_SIZE": 3, "DB_MAX_OVERFLOW": 2})
    engine = create_db_engine(custom)
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.pool.size() == 3  # type: ignore[attr-defined]
    finally:
        engine.dispose()


def test_session_factory_keeps_objects_loaded_after_commit(settings: Settings) -> None:
    engine = create_db_engine(settings)
    try:
        factory = create_session_factory(engine)
        assert factory.kw["expire_on_commit"] is False
        assert factory.kw["autoflush"] is False
    finally:
        engine.dispose()
