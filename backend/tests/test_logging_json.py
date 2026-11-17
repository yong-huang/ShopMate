"""Tests for the JSON-lines log formatter (NFR-4 observability)."""

import json
import logging

from app.logging_config import JsonLineFormatter


def test_record_formats_as_single_parseable_json_line() -> None:
    formatter = JsonLineFormatter()
    record = logging.LogRecord(
        name="shopmate",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )

    line = formatter.format(record)

    assert "\n" not in line
    parsed = json.loads(line)
    assert parsed["event"] == "hello world"
    assert parsed["level"] == "info"
    assert parsed["logger"] == "shopmate"
    assert isinstance(parsed["ts"], str)


def test_extra_fields_land_in_the_json_payload() -> None:
    formatter = JsonLineFormatter()
    record = logging.LogRecord(
        name="shopmate",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="http_request",
        args=(),
        exc_info=None,
    )
    record.duration_ms = 12.5
    record.status_code = 200

    parsed = json.loads(formatter.format(record))

    assert parsed["duration_ms"] == 12.5
    assert parsed["status_code"] == 200


def test_exception_information_is_included() -> None:
    formatter = JsonLineFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="shopmate",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="chat_turn",
            args=(),
            exc_info=sys.exc_info(),
        )

    line = formatter.format(record)

    parsed = json.loads(line)  # still a single valid JSON line
    assert parsed["error"] == "boom"
    assert parsed["error_type"] == "ValueError"


def test_non_serializable_extra_is_stringified() -> None:
    formatter = JsonLineFormatter()
    record = logging.LogRecord(
        name="shopmate",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="event",
        args=(),
        exc_info=None,
    )
    record.weird = object()  # not JSON-serializable

    parsed = json.loads(formatter.format(record))

    assert isinstance(parsed["weird"], str)


def test_configure_logging_silences_uvicorns_plain_output() -> None:
    """The JSON stream must stay machine-parseable (NFR-4).

    uvicorn installs a plain-text handler on `uvicorn.access` before the app
    is imported; configure_logging removes it so access records propagate to
    the root JSON handler instead of doubling the stream in plain text.
    """
    import logging

    from app.logging_config import configure_logging

    # Simulate uvicorn's dictConfig faithfully: the plain-text handler lives
    # on the `uvicorn` logger itself with propagation disabled, and children
    # (uvicorn.error, uvicorn.access) inherit it through the ancestor chain.
    uvicorn = logging.getLogger("uvicorn")
    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn.addHandler(logging.StreamHandler())
    uvicorn.propagate = False
    uvicorn_access.addHandler(logging.StreamHandler())
    uvicorn_access.propagate = False

    configure_logging()

    for uv_logger in (uvicorn, uvicorn_error, uvicorn_access):
        assert uv_logger.handlers == []
        assert uv_logger.propagate is True
