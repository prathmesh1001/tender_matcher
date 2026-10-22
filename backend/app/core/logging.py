"""Structured JSON logging with per-request/run context.

Context fields (`run_id`, `tender_id`, `manufacturer`) are bound with `log_context(...)` and are
attached automatically to every record emitted inside that block, including from worker code.
"""

import json
import logging
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Final

CONTEXT_FIELDS: Final[tuple[str, ...]] = ("run_id", "tender_id", "manufacturer")

_log_context: ContextVar[Mapping[str, str]] = ContextVar("log_context", default={})  # noqa: B039

# Attributes present on every LogRecord; anything else came from `extra=` and is emitted.
_RESERVED_ATTRS: Final[frozenset[str]] = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys() | {"message", "asctime"}
)


@contextmanager
def log_context(**fields: str) -> Iterator[None]:
    """Bind context fields to all log records emitted inside the `with` block.

    Args:
        **fields: Values for any of `CONTEXT_FIELDS`, e.g. `run_id="r1"`.

    Yields:
        None. The previous context is restored on exit, even on error.

    Raises:
        ValueError: If a field name is not one of `CONTEXT_FIELDS`.
    """
    unknown = set(fields) - set(CONTEXT_FIELDS)
    if unknown:
        raise ValueError(f"Unknown log context fields: {sorted(unknown)}")
    token = _log_context.set({**_log_context.get(), **fields})
    try:
        yield
    finally:
        _log_context.reset(token)


def current_log_context() -> dict[str, str]:
    """Return a copy of the context fields bound in the current execution context."""
    return dict(_log_context.get())


class JsonFormatter(logging.Formatter):
    """Render each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a record with timestamp, level, logger, message, context and extras.

        Args:
            record: The record to format.

        Returns:
            A JSON string with no trailing newline.
        """
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **current_log_context(),
            **_extra_fields(record),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def _extra_fields(record: logging.LogRecord) -> dict[str, object]:
    """Return the attributes that were passed to the logger via `extra=`."""
    return {k: v for k, v in vars(record).items() if k not in _RESERVED_ATTRS}


def configure_logging(level: str, *, json_output: bool) -> None:
    """Configure the root logger with a single stdout handler.

    Idempotent: calling it again replaces the handler instead of adding a second one.

    Args:
        level: Log level name, e.g. `"INFO"`.
        json_output: Use `JsonFormatter` when true, a plain text format otherwise.
    """
    handler = logging.StreamHandler(sys.stdout)
    formatter: logging.Formatter = (
        JsonFormatter()
        if json_output
        else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
