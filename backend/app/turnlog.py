"""Per-request / per-chat-turn logging context (NFR-4).

One structured `chat_turn` record is emitted per chat turn with tool hops,
latencies, token usage, and validation outcomes. The request middleware
(`app.request_logging`) opens a request scope; a chat endpoint activates it
as a turn and attaches data::

    turn = begin_turn(session_id=session_id)
    turn.record_tool_hop("search_products", latency_ms=12.3)
    turn.record_token_usage(prompt_tokens=120, completion_tokens=80)
    turn.record_validation(outcome="passed")
    turn.finish(status="ok")

`finish` is optional inside a request — the request scope finalizes any
unfinished turn (`incomplete`, or `error` if the request raised), so exactly
one record always lands. Outside a request (e.g. background profile
extraction), `begin_turn` creates a standalone turn and `finish` is required.
"""

from contextvars import ContextVar
from time import perf_counter
from typing import Any
import uuid

from app.logging_config import get_logger

logger = get_logger()


class TurnContext:
    """Accumulator for one request, optionally activated as a chat turn."""

    def __init__(
        self,
        request_id: str | None = None,
        session_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.turn_id = uuid.uuid4().hex
        self.request_id = request_id
        self.session_id = session_id
        self.context: dict[str, Any] = dict(context or {})
        self.is_turn = False
        self.finished = False
        self._started_perf = perf_counter()
        self.tool_hops: list[dict[str, Any]] = []
        self.token_usage: dict[str, Any] | None = None
        self.validation: dict[str, Any] | None = None

    # --- hooks -------------------------------------------------------------
    def record_tool_hop(
        self, tool: str, latency_ms: float, ok: bool = True, **extra: Any
    ) -> None:
        """Record one tool execution with its measured latency in ms."""
        self.tool_hops.append(
            {
                "tool": tool,
                "latency_ms": round(float(latency_ms), 3),
                "ok": ok,
                **extra,
            }
        )

    def record_token_usage(
        self, prompt_tokens: int, completion_tokens: int, **extra: Any
    ) -> None:
        """Record the LLM token usage observed during the turn."""
        self.token_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            **extra,
        }

    def record_validation(self, outcome: str, **extra: Any) -> None:
        """Record a guardrail validation outcome (e.g. passed/retried/stripped)."""
        self.validation = {"outcome": outcome, **extra}

    # --- lifecycle -----------------------------------------------------------
    def activate(
        self, session_id: str | None = None, context: dict[str, Any] | None = None
    ) -> "TurnContext":
        """Mark this request scope as a chat turn (idempotent)."""
        self.is_turn = True
        if session_id is not None:
            self.session_id = session_id
        for key, value in (context or {}).items():
            self.context.setdefault(key, value)
        return self

    def snapshot(self) -> dict[str, Any]:
        """The structured payload describing this turn so far."""
        return {
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "context": dict(self.context),
            "duration_ms": round((perf_counter() - self._started_perf) * 1000, 3),
            "tool_hops": list(self.tool_hops),
            "token_usage": self.token_usage,
            "validation": self.validation,
        }

    def finish(
        self,
        status: str = "ok",
        error: BaseException | str | None = None,
    ) -> dict[str, Any] | None:
        """Emit the single `chat_turn` record (idempotent; first call wins)."""
        if self.finished:
            return None
        self.finished = True
        payload = self.snapshot()
        payload["status"] = status
        if error is not None:
            payload["error"] = str(error)
            payload["error_type"] = (
                type(error).__name__ if isinstance(error, BaseException) else error
            )
        logger.info("chat_turn", extra=payload)
        return payload


_active: ContextVar[TurnContext | None] = ContextVar("shopmate_turn", default=None)


class RequestScope:
    """Context manager binding a fresh turn context to the current request."""

    def __init__(self, request_id: str | None) -> None:
        self.request_id = request_id
        self.turn: TurnContext | None = None
        self._token: Any = None

    def __enter__(self) -> "RequestScope":
        self.turn = TurnContext(request_id=self.request_id)
        self._token = _active.set(self.turn)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        # Safety net: a chat turn that was begun but never finished still
        # produces exactly one record, so NFR-4 holds under any outcome.
        if self.turn is not None and self.turn.is_turn and not self.turn.finished:
            if exc_type is not None:
                self.turn.finish(status="error", error=exc)
            else:
                self.turn.finish(status="incomplete")
        if self._token is not None:
            _active.reset(self._token)

    def correlation(self) -> dict[str, Any]:
        """Fields the access record carries to link back to the chat turn."""
        if self.turn is not None and self.turn.is_turn:
            return {"turn_id": self.turn.turn_id}
        return {}


def request_scope(request_id: str | None = None) -> RequestScope:
    """Open the request-scoped turn context."""
    return RequestScope(request_id)


def begin_turn(
    session_id: str | None = None,
    context: dict[str, Any] | None = None,
    **context_extra: Any,
) -> TurnContext:
    """Activate the current request scope as a chat turn.

    Inside a request this activates the middleware-created context; outside
    one (background tasks) it creates a standalone turn that the caller must
    `finish()`. Extra keyword arguments are merged into the turn context.
    """
    merged = {**(context or {}), **context_extra}
    turn = _active.get()
    if turn is None or turn.finished:
        # A finished turn is dead weight; never extend it with a new turn.
        turn = TurnContext(session_id=session_id, context=merged)
        turn.activate(session_id=session_id, context=merged)
        _active.set(turn)
        return turn
    return turn.activate(session_id=session_id, context=merged)


def current_turn() -> TurnContext | None:
    """The turn context for the current request/task (or None outside one)."""
    return _active.get()
