"""Unit tests for structured JSON logging."""

import json
import logging

from observability.logging_config import JSONFormatter


def _make_record(msg="hello", extra=None):
    record = logging.LogRecord(
        name="observability.test", level=logging.INFO, pathname=__file__, lineno=1, msg=msg, args=(), exc_info=None
    )
    if extra:
        for key, value in extra.items():
            setattr(record, key, value)
    return record


def test_format_produces_valid_json():
    formatted = JSONFormatter().format(_make_record())
    data = json.loads(formatted)
    assert data["message"] == "hello"
    assert data["level"] == "INFO"
    assert data["logger"] == "observability.test"
    assert "timestamp" in data


def test_extra_fields_are_included():
    formatted = JSONFormatter().format(_make_record(extra={"trace_id": "abc123", "span_kind": "llm"}))
    data = json.loads(formatted)
    assert data["trace_id"] == "abc123"
    assert data["span_kind"] == "llm"


def test_extra_fields_are_redacted():
    formatted = JSONFormatter().format(_make_record(extra={"api_key": "super-secret-value"}))
    data = json.loads(formatted)
    assert data["api_key"] == "***REDACTED***"
    assert "super-secret-value" not in formatted


def test_exception_info_is_included_when_present():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="t", level=logging.ERROR, pathname=__file__, lineno=1, msg="failed", args=(), exc_info=sys.exc_info()
        )
    formatted = JSONFormatter().format(record)
    data = json.loads(formatted)
    assert "boom" in data["exception"]


def test_format_never_raises_even_with_unserializable_extra():
    class Unserializable:
        def __str__(self):
            raise RuntimeError("cannot stringify")

    formatted = JSONFormatter().format(_make_record(extra={"weird": Unserializable()}))
    json.loads(formatted)  # must still be valid JSON
