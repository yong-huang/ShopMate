"""JSON-lines structured logging for the ShopMate backend (NFR-4).

Every log record is rendered as exactly one machine-parseable JSON line on
stdout, so `docker logs` / log collectors can consume the stream directly.
"""

import datetime as _dt
import json
import logging
import sys

LOGGER_NAME = "shopmate"

# Standard LogRecord attributes that are either already mapped to explicit
# JSON fields or carry no value for log consumers.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "stacklevel",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonLineFormatter(logging.Formatter):
    """Render each record as one line of JSON with its extra fields inline."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": _dt.datetime.fromtimestamp(
                record.created, tz=_dt.timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload.setdefault(key, value)
        if record.exc_info:
            payload.setdefault("error", str(record.exc_info[1]))
            payload.setdefault("error_type", record.exc_info[0].__name__)
        return _dumps(payload)


def _dumps(payload: dict) -> str:
    return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """Attach the JSON-lines handler to the root logger (idempotent).

    Attaching at the root keeps uvicorn's own records (which propagate) in
    the same JSON stream, which is what `docker logs` expects. uvicorn's
    plain-text access-log handler is removed so the stream stays pure JSON.
    """
    # uvicorn configures its loggers (via dictConfig) before importing the
    # app: the plain-text handler lives on the `uvicorn` logger itself with
    # propagation disabled, and uvicorn.error / uvicorn.access inherit it
    # through the ancestor chain. Clear all three and re-enable propagation
    # so every uvicorn record flows through the root JSON formatter instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler.formatter, JsonLineFormatter):
            root.setLevel(level)
            return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLineFormatter())
    root.addHandler(handler)
    root.setLevel(level)


def get_logger() -> logging.Logger:
    """The application logger used for all structured ShopMate events."""
    return logging.getLogger(LOGGER_NAME)
