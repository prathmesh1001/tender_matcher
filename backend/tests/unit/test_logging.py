import json
import logging
import sys

import pytest

from app.core.logging import JsonFormatter, configure_logging, current_log_context, log_context


def _record(msg: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_emits_single_line_json_with_core_fields() -> None:
    line = JsonFormatter().format(_record("hello"))
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert "timestamp" in payload


def test_formatter_includes_extra_fields() -> None:
    payload = json.loads(JsonFormatter().format(_record(doc_id="d1", page=3)))
    assert payload["doc_id"] == "d1"
    assert payload["page"] == 3


def test_formatter_includes_bound_context() -> None:
    with log_context(run_id="r1", manufacturer="KSB"):
        payload = json.loads(JsonFormatter().format(_record()))
    assert payload["run_id"] == "r1"
    assert payload["manufacturer"] == "KSB"


def test_formatter_includes_exception_traceback() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "x", None, sys.exc_info())
    payload = json.loads(JsonFormatter().format(record))
    assert "RuntimeError: boom" in payload["exception"]


def test_log_context_nests_and_restores() -> None:
    with log_context(run_id="r1"):
        with log_context(tender_id="t1"):
            assert current_log_context() == {"run_id": "r1", "tender_id": "t1"}
        assert current_log_context() == {"run_id": "r1"}
    assert current_log_context() == {}


def test_log_context_restores_after_error() -> None:
    with pytest.raises(KeyError), log_context(run_id="r1"):
        raise KeyError("x")
    assert current_log_context() == {}


def test_log_context_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="colour"), log_context(colour="x"):
        pass


def test_configure_logging_is_idempotent() -> None:
    configure_logging("DEBUG", json_output=True)
    configure_logging("WARNING", json_output=True)
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    assert root.level == logging.WARNING
