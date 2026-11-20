"""POST /api/chat — the streaming chat endpoint (System Design 3.7).

SSE contract, in wire order: ``session`` → ``token``* → ``tool_call``* →
``cart_update``? → ``product_card``* / ``comparison``? → ``done``; failures
end the stream with one ``error`` event (``validation`` | ``model`` |
``internal``). ``cart_update`` (T06) carries the freshest tool-attached cart
summary after cart-affecting turns (System Design 3.7).

Cards on the wire are the guardrail-approved payloads from the graph state
(System Design 3.5) — the tool-verified ground truth attached at
tool-execution time, never model prose.

Responsibilities (the agent graph stays pure per §3.2):
- anonymous identity: issue/reuse the ``shopmate_sid`` HttpOnly cookie
- persist user + assistant (incl. tool calls) + tool messages (§3.8)
- run one bounded agent turn per request (thread per session)
- emit the turn-log ``chat_turn`` record (NFR-4) with hops, token usage, and
  the guardrail verdict incl. the pre-strip failing card ids (eval audit)
"""

import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field, field_validator

from app.agent.cards import detect_language
from app.agent.graph import (
    AgentToolDone,
    AgentToolProgress,
    AgentToken,
    AgentUsage,
    AgentValidation,
    stream_agent_turn,
)
from app.cart.render import render_cart_summary_text
from app.cart.service import cart_summary as read_cart_summary
from app.db import (
    get_or_create_session,
    get_or_create_user,
    record_message,
    set_session_language,
)
from app.gateway.errors import GatewayError
from app.gateway.tools import ToolCall
from app.logging_config import get_logger
from app.profile.render import render_profile_block
from app.profile.service import end_session_if_idle, get_profile_payload, schedule_extraction
from app.turnlog import TurnContext, current_turn

router = APIRouter(tags=["chat"])

SESSION_COOKIE = "shopmate_sid"

logger = get_logger()


def _sse(event: str, data: dict) -> str:
    """One SSE frame in the §3.7 shape."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _message_to_row(message) -> tuple[str, str | None, list | None]:
    """Map one graph-state message to (role, content, tool_calls JSON)."""
    if isinstance(message, HumanMessage):
        return "user", str(message.content), None
    if isinstance(message, ToolMessage):
        return "tool", str(message.content), None
    if isinstance(message, AIMessage):
        tool_calls = None
        if getattr(message, "tool_calls", None):
            # Same wire shape the gateway replays (tests/gateway/test_messages.py).
            tool_calls = [
                ToolCall(
                    id=call.get("id"),
                    name=call["name"],
                    arguments=dict(call.get("args") or {}),
                ).to_wire()
                for call in message.tool_calls
            ]
        return "assistant", str(message.content or ""), tool_calls
    return "system", str(getattr(message, "content", "") or ""), None


class ChatRequest(BaseModel):
    """Request body per System Design 3.7: {session_id, message}."""

    session_id: str = Field(min_length=8, max_length=64)
    message: str = Field(min_length=1)

    @field_validator("session_id")
    @classmethod
    def _uuid(cls, value: str) -> str:
        try:
            uuid.UUID(value)
        except ValueError:
            raise ValueError("session_id must be a UUID") from None
        return value


def _final_assistant_text(new_messages: list) -> str:
    """The turn's final assistant text — the situational recommendation."""
    text = ""
    for message in new_messages:
        if isinstance(message, AIMessage) and not getattr(message, "tool_calls", None):
            text = str(message.content or "")
    return text


async def _persist_turn_messages(sessionmaker, session_id: str, new_messages: list) -> int | None:
    """Persist this turn's assistant/tool messages; return final message id.

    The user message is already persisted before the graph runs, so its
    replayed ``HumanMessage`` copy in the graph state is skipped here.
    """
    final_id: int | None = None
    async with sessionmaker() as db:
        for message in new_messages:
            if isinstance(message, HumanMessage):
                continue
            role, content, tool_calls = _message_to_row(message)
            row = await record_message(
                db, session_id=session_id, role=role, content=content, tool_calls=tool_calls
            )
            final_id = row.id
        await db.commit()
    return final_id


@router.post("/chat")
async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
    """Stream one Maya turn as SSE (session/token/tool_call/done|error)."""
    settings = request.app.state.settings
    sessionmaker = request.app.state.sessionmaker

    # --- identity (anonymous, cookie-linked) --------------------------------
    issued_sid: str | None = None
    cookie_sid = request.cookies.get(SESSION_COOKIE)
    if cookie_sid is None:
        cookie_sid = issued_sid = uuid.uuid4().hex
    # §3.6 session-touch: lazy idle-TTL check, run BEFORE this turn's message
    # lands. If the previous activity on the session is older than the TTL,
    # the session is treated as ended and its profile extraction is scheduled
    # in the background (ADR-007) — never awaited on the request path.
    idle_extraction_id: str | None = None
    async with sessionmaker() as db:
        await get_or_create_user(db, cookie_sid)
        if await end_session_if_idle(
            db, body.session_id, ttl_min=settings.session_idle_ttl_min
        ):
            idle_extraction_id = body.session_id
        await get_or_create_session(db, body.session_id, cookie_sid)
        # sessions.lang uses the same shared heuristic the eval runner checks
        # (app.agent.cards.detect_language) so production and eval agree.
        lang = detect_language(body.message)
        if lang:
            await set_session_language(db, body.session_id, lang)
        await record_message(
            db, session_id=body.session_id, role="user", content=body.message
        )
        await db.commit()

    graph = request.app.state.graph
    # Correlate the chat turn with the request's access record where possible.
    scope_turn = current_turn()
    request_id = scope_turn.request_id if scope_turn is not None else None

    # §3.6 injection: render this shopper's profile into the context block
    # (compact, capped). A DB read only — extraction never runs here.
    profile_block = render_profile_block(
        await get_profile_payload(sessionmaker, cookie_sid),
        max_chars=settings.profile_injection_max_chars,
    )
    extraction_client = getattr(request.app.state, "extraction_client", None)
    if idle_extraction_id is not None and extraction_client is not None:
        logger.info(
            "profile_extraction_scheduled",
            extra={"session_id": idle_extraction_id, "trigger": "idle_ttl"},
        )
        schedule_extraction(sessionmaker, extraction_client, idle_extraction_id)

    # §3.2 injection: render this shopper's cart into the CART_SUMMARY block
    # (compact, capped). A DB read only — the tools mutate the cart later in
    # the turn and the fresh state reaches the shopper via cart_update.
    async with sessionmaker() as db:
        cart_block = render_cart_summary_text(
            await read_cart_summary(db, request.app.state.catalog, cookie_sid)
        )

    async def event_stream() -> AsyncIterator[str]:
        # A chat turn is a standalone TurnContext: the response body streams
        # after the logging middleware's scope has already exited, so its
        # scope cannot own this turn's lifecycle (it would finalize the turn
        # as "incomplete" before any token streams). `finish` is mandatory
        # here and guarantees exactly one chat_turn record per turn (NFR-4).
        turn = TurnContext(
            request_id=request_id,
            session_id=body.session_id,
            context={"provider": settings.llm_provider, "model": settings.llm_model},
        )
        yield _sse("session", {"session_id": body.session_id, "user_id": cookie_sid})
        status = "ok"
        if graph is None:
            status = "error"
            yield _sse(
                "error",
                {
                    "code": "model",
                    "message": "No chat model is configured. Set LLM_API_KEY "
                    "(cloud preset) or LLM_PROVIDER=ollama for the local preset.",
                },
            )
            turn.finish(status=status, error="provider_not_configured")
            return
        config = {"configurable": {"thread_id": body.session_id, "user_id": cookie_sid}}
        try:
            state_before = await graph.aget_state(config)
            base_count = len(state_before.values.get("messages", []))
            async for agent_event in stream_agent_turn(
                graph,
                body.session_id,
                body.message,
                profile_block=profile_block,
                cart_summary=cart_block,
                user_id=cookie_sid,
            ):
                if isinstance(agent_event, AgentToken):
                    yield _sse("token", {"text": agent_event.text})
                elif isinstance(agent_event, AgentToolProgress):
                    yield _sse(
                        "tool_call",
                        {"name": agent_event.name, "args_summary": agent_event.args_summary},
                    )
                elif isinstance(agent_event, AgentToolDone):
                    turn.record_tool_hop(
                        agent_event.name, latency_ms=agent_event.latency_ms, ok=agent_event.ok
                    )
                elif isinstance(agent_event, AgentUsage):
                    turn.record_token_usage(
                        prompt_tokens=agent_event.prompt_tokens,
                        completion_tokens=agent_event.completion_tokens,
                    )
                elif isinstance(agent_event, AgentValidation):
                    turn.record_validation(
                        agent_event.outcome,
                        failed_product_ids=agent_event.failed_product_ids,
                        card_count=agent_event.card_count,
                    )
            state_after = await graph.aget_state(config)
            new_messages = state_after.values.get("messages", [])[base_count:]
            # cart_update (3.7): after cart-affecting tool hops the freshest
            # tool-attached cart summary goes on the wire (items + total) so
            # the storefront and the chat widget agree with the server state.
            cart_events = state_after.values.get("cart_events") or []
            if cart_events:
                yield _sse("cart_update", cart_events[-1])
            # Validated cards/comparison (3.5) stream after the text, before
            # done — payloads are the catalog ground truth, never model prose.
            for card in state_after.values.get("validated_cards") or []:
                yield _sse("product_card", card)
            comparison = state_after.values.get("comparison")
            if comparison is not None:
                yield _sse(
                    "comparison",
                    {
                        "product_ids": comparison.get("product_ids", []),
                        "matrix": comparison.get("matrix"),
                        "recommendation": _final_assistant_text(new_messages),
                    },
                )
            final_id = await _persist_turn_messages(sessionmaker, body.session_id, new_messages)
            yield _sse("done", {"message_id": final_id})
        except GatewayError as exc:
            status = "error"
            yield _sse("error", {"code": "model", "message": str(exc)})
            turn.finish(status=status, error=exc)
            return
        except Exception as exc:  # noqa: BLE001 — streamed as honest internal error
            status = "error"
            logger.exception("chat_stream_failed")
            yield _sse("error", {"code": "internal", "message": "Internal chat error."})
            turn.finish(status=status, error=exc)
            return
        turn.finish(status=status)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    response = StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=headers,
    )
    if issued_sid is not None:
        response.set_cookie(
            SESSION_COOKIE,
            issued_sid,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=365 * 24 * 3600,
        )
    return response
