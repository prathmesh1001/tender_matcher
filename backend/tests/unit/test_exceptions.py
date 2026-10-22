from collections.abc import Iterator

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.core.exceptions import (
    AppError,
    ExternalServiceError,
    NotFoundError,
    TenderParseError,
)


@pytest.fixture
def failing_client(app: FastAPI) -> Iterator[TestClient]:
    """App with throwaway routes that raise, to exercise the handlers end to end."""
    router = APIRouter()

    @router.get("/_test/not-found")
    def not_found() -> None:
        raise NotFoundError("Tender 42 not found", details={"tender_id": 42})

    @router.get("/_test/parse")
    def parse() -> None:
        raise TenderParseError("No specification table found")

    @router.get("/_test/crash")
    def crash() -> None:
        raise RuntimeError("secret internal detail")

    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_app_error_keeps_message_and_copies_details() -> None:
    details = {"k": "v"}
    err = NotFoundError("missing", details=details)
    details["k"] = "mutated"
    assert err.message == "missing"
    assert err.details == {"k": "v"}
    assert str(err) == "missing"


@pytest.mark.parametrize(
    ("exc_type", "status_code", "code"),
    [
        (NotFoundError, 404, "not_found"),
        (TenderParseError, 422, "tender_parse_error"),
        (ExternalServiceError, 502, "external_service_error"),
    ],
)
def test_subclasses_declare_status_and_code(
    exc_type: type[AppError], status_code: int, code: str
) -> None:
    assert issubclass(exc_type, AppError)
    assert exc_type.status_code == status_code
    assert exc_type.code == code


def test_domain_error_is_mapped_to_typed_body(failing_client: TestClient) -> None:
    response = failing_client.get("/_test/not-found")
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Tender 42 not found",
            "details": {"tender_id": 42},
        }
    }


def test_parse_error_is_422(failing_client: TestClient) -> None:
    response = failing_client.get("/_test/parse")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "tender_parse_error"


def test_unexpected_error_is_500_without_leaking_internals(failing_client: TestClient) -> None:
    response = failing_client.get("/_test/crash")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret" not in response.text
