"""Domain exceptions and their single mapping to HTTP responses.

Business code raises these; it never builds HTTP responses itself. `register_exception_handlers`
is the only place where exceptions become status codes.
"""

import logging
from collections.abc import Mapping
from typing import ClassVar

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.schemas.errors import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for all expected, domain-level errors."""

    status_code: ClassVar[int] = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: ClassVar[str] = "app_error"

    def __init__(self, message: str, *, details: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, object] = dict(details or {})


class NotFoundError(AppError):
    """A requested resource (tender, run, document, …) does not exist."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class InvalidInputError(AppError):
    """Input is well-formed but violates a business rule."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "invalid_input"


class TenderParseError(AppError):
    """A tender document could not be parsed into requirements."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "tender_parse_error"


class DocumentParseError(AppError):
    """A manufacturer document could not be parsed."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "document_parse_error"


class SourceRegistryError(AppError):
    """A `sources.yaml` entry is invalid, or does not match the file on disk."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "source_registry_error"


class NoEvidenceError(AppError):
    """No verifiable evidence was found for a requirement."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "no_evidence"


class OntologyError(AppError):
    """An ontology YAML file is missing, malformed, or inconsistent."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "ontology_error"


class UnitConversionError(AppError):
    """A value's unit is unknown or cannot be converted to the attribute's canonical unit."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "unit_conversion_error"


class LinkFetchError(AppError):
    """A document linked from a tender could not be downloaded."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "link_fetch_error"


class ExternalServiceError(AppError):
    """An external dependency (LLM, OCR, embedder, …) failed or returned invalid output."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "external_service_error"


def _error_response(status_code: int, body: ErrorBody) -> JSONResponse:
    """Build the JSON response shared by all error handlers."""
    return JSONResponse(
        status_code=status_code, content=ErrorResponse(error=body).model_dump(mode="json")
    )


async def handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    """Map an `AppError` to its declared status code and a typed error body.

    Args:
        request: The failing request.
        exc: The raised exception (always an `AppError`; typed broadly for Starlette).

    Returns:
        A JSON error response.
    """
    assert isinstance(exc, AppError)  # noqa: S101 - guaranteed by handler registration
    logger.warning(
        "Request failed: %s", exc.message, extra={"error_code": exc.code, "path": request.url.path}
    )
    body = ErrorBody(code=exc.code, message=exc.message, details=exc.details)
    return _error_response(exc.status_code, body)


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Log an unhandled exception with traceback and return a generic 500 without internals.

    Args:
        request: The failing request.
        exc: The unhandled exception.

    Returns:
        A JSON 500 response.
    """
    logger.error("Unhandled error on %s", request.url.path, exc_info=exc)
    body = ErrorBody(code="internal_error", message="Internal server error.")
    return _error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, body)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the domain and fallback exception handlers to the app.

    Args:
        app: The FastAPI application.
    """
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
