"""Shared pytest fixtures."""

import os
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app

# Matches the docker-compose `db` service; override with TEST_DATABASE_URL.
DEFAULT_TEST_DATABASE_URL = "postgresql+psycopg://tender:tender@localhost:5433/tender_matcher_test"


def get_test_database_url() -> str:
    """URL of the database the test suite is allowed to wipe."""
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


@pytest.fixture
def settings() -> Settings:
    """Deterministic settings that ignore the developer's environment and `.env` file."""
    return Settings(
        _env_file=None,
        APP_NAME="Tender Matcher Test",
        APP_VERSION="9.9.9",
        ENVIRONMENT=Environment.TEST,
        LOG_JSON=False,
        DATABASE_URL=get_test_database_url(),  # type: ignore[arg-type]
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
