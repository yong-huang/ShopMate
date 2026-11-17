"""Turn-log capture: ``chat_turn`` records from the ShopMate JSON logger.

The chat endpoint emits exactly one ``chat_turn`` record per turn (NFR-4)
with the evidence the eval harness needs but the SSE wire does not carry:
tool hops (executed tools + ok flags + latencies), token usage, and the
guardrail validation record whose ``failed_product_ids`` are the PRE-strip
card ids — the §3.1 grounding audit trail.

The capture attaches a logging handler to the ShopMate logger for the run's
lifetime and indexes records by ``session_id`` (each scenario gets its own
session, so mapping is unambiguous).
"""

from __future__ import annotations

import logging
from typing import Any

from app.logging_config import LOGGER_NAME

# The payload keys a chat_turn record carries (app.turnlog.snapshot + status).
_RECORD_KEYS = (
    "turn_id",
    "request_id",
    "session_id",
    "status",
    "duration_ms",
    "tool_hops",
    "token_usage",
    "validation",
    "error",
    "error_type",
    "context",
)


class TurnLogCapture:
    """Collect ``chat_turn`` records, indexed by session id."""

    def __init__(self) -> None:
        self._records: list[dict[str, Any]] = []
        self._handler: logging.Handler | None = None

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._handler is not None:
            return
        capture = self

        class _Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if record.msg != "chat_turn":
                    return
                capture._records.append(
                    {key: record.__dict__.get(key) for key in _RECORD_KEYS}
                )

        self._handler = _Handler(level=logging.INFO)
        logging.getLogger(LOGGER_NAME).addHandler(self._handler)

    def stop(self) -> None:
        if self._handler is not None:
            logging.getLogger(LOGGER_NAME).removeHandler(self._handler)
            self._handler = None

    def __enter__(self) -> "TurnLogCapture":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # --- access ----------------------------------------------------------------

    def chat_turns(self, session_id: str) -> list[dict[str, Any]]:
        """All chat_turn records for one session, in emission order."""
        return [
            record for record in self._records if record.get("session_id") == session_id
        ]

    def latest_turn(self, session_id: str) -> dict[str, Any] | None:
        """The most recent chat_turn record for the session (or None)."""
        turns = self.chat_turns(session_id)
        return turns[-1] if turns else None
