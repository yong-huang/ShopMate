"""Maya agent state machine (System Design 3.2, ADR-002).

Topology: ``START → chatbot ──no tool calls──► validate ──► END`` with the
``tools`` loop in between; at most ``MAX_TOOL_HOPS`` tool hops per turn. The
``validate`` node is a passthrough in this ticket — guardrail logic is T05 —
but the node exists so the graph shape is final.

Nodes are pure functions over ``AgentState``; the only I/O boundary is the
injected ``ChatClient`` (ADR-001) and the tool registry. One SQLite thread
per session (``SqliteSaver`` family checkpointer) keeps conversation state
alive across restarts; the SSE layer additionally persists raw messages to
the ``messages`` table (System Design 3.8).
"""

import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict, Union

import aiosqlite
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.agent.cards import CardIssue, card_from_product, parse_card_blocks, validate_card
from app.agent.prompts import compile_maya_prompt
from app.agent.tools import MAX_TOOL_HOPS, ToolRegistry, summarize_arguments, tool_registry
from app.catalog.adapter import CatalogAdapter, JsonCatalogAdapter
from app.gateway.client import ChatClient
from app.gateway.errors import ToolCallDegradationError
from app.gateway.events import FinishEvent
from app.gateway.messages import ChatMessage
from app.gateway.tools import ToolCall

DEFAULT_TEMPERATURE = 0.3

# Honest fallbacks (bilingual) for the two loop-proofing edge cases.
TOOL_BUDGET_MESSAGE = (
    "I've hit my tool-call budget for this turn, so I'll answer from what I "
    "have so far.\n\n这一轮的工具调用已达上限，我先基于已有信息继续。"
)
DEGRADATION_MESSAGE = (
    "I'm sorry — this model can't run the shop tools, so I can't look up the "
    "catalog right now. Try a tool-capable model or provider.\n\n"
    "抱歉：当前模型无法调用商店工具，暂时无法查询商品目录。"
)


# Honest corrective instruction for the one guardrail retry (3.5).
CORRECTIVE_TEMPLATE = (
    "Guardrail: your last reply referenced card(s) {failed} that failed "
    "catalog validation — a non-catalog product id and/or a price/rating "
    "that does not match tool output. Use tool results only. Rewrite the "
    "reply with correct product ids, prices and ratings exactly as your "
    "tools returned them, or reply without any card block."
)


# --- State (System Design 3.2) -------------------------------------------


class ToolResult(TypedDict):
    """One executed tool hop, as recorded by the ``tools`` node."""

    name: str
    ok: bool
    content: str
    latency_ms: float


class AgentState(TypedDict):
    """Per-session-thread graph state (System Design 3.2)."""

    messages: Annotated[list[AnyMessage], add_messages]
    tool_results: list[ToolResult]  # last hop's tool outputs
    pending_cards: list[dict[str, Any]]  # cards attached by tools at execution time (3.3)
    proposed_cards: list[dict[str, Any]]  # card claims parsed from the model's reply (3.5)
    validated_cards: list[dict[str, Any]]  # guardrail-approved payloads (3.5)
    comparison: dict[str, Any] | None  # validated comparison payload (3.3)
    stripped_card_ids: list[str]  # pre-strip audit trail for the eval harness
    retried_validation: bool  # the one corrective retry (3.5) already spent
    validation_done: bool  # validate finalized this turn
    cart_events: list[dict[str, Any]]  # cart summaries after cart-affecting tools (T06, 3.7)
    # Rendered shopper profile block (T07, §3.6 injection): set per turn by
    # the chat endpoint from the profile store; the chatbot node renders it
    # into the system prompt's PROFILE_BLOCK seam.
    profile_block: str
    # Rendered cart summary (T06, §3.2): set per turn by the chat endpoint
    # from the cart store; the chatbot node renders it into the system
    # prompt's CART_SUMMARY seam.
    cart_summary: str


# --- Events yielded by stream_agent_turn ----------------------------------


class AgentToken(BaseModel):
    """One assistant text delta (SSE ``token``)."""

    kind: Literal["token"] = "token"
    text: str


class AgentToolProgress(BaseModel):
    """A tool call is starting (SSE ``tool_call`` progress indicator)."""

    kind: Literal["tool_progress"] = "tool_progress"
    name: str
    args_summary: str


class AgentToolDone(BaseModel):
    """One tool hop finished (turn-log telemetry; not an SSE event type)."""

    kind: Literal["tool_done"] = "tool_done"
    name: str
    ok: bool
    latency_ms: float


class AgentUsage(BaseModel):
    """Provider token usage for the turn (turn-log telemetry)."""

    kind: Literal["usage"] = "usage"
    prompt_tokens: int
    completion_tokens: int


class AgentValidation(BaseModel):
    """The guardrail verdict for one turn (turn-log telemetry; 3.5).

    ``failed_product_ids`` are the PRE-strip failing card ids of the whole
    turn — the audit trail the eval harness reads to catch grounding
    failures the guardrail absorbed.
    """

    kind: Literal["validation"] = "validation"
    outcome: Literal["passed", "retried", "stripped"]
    failed_product_ids: list[str]
    card_count: int


AgentEvent = Annotated[
    Union[AgentToken, AgentToolProgress, AgentToolDone, AgentUsage, AgentValidation],
    Field(discriminator="kind"),
]


# --- Message conversion (langchain state <-> gateway wire) -----------------


def _to_chat_message(message: AnyMessage) -> ChatMessage:
    """Map one langchain state message to the gateway ``ChatMessage``."""
    if isinstance(message, SystemMessage):
        return ChatMessage(role="system", content=str(message.content))
    if isinstance(message, HumanMessage):
        return ChatMessage(role="user", content=str(message.content))
    if isinstance(message, ToolMessage):
        return ChatMessage(
            role="tool",
            tool_call_id=message.tool_call_id,
            content=str(message.content),
        )
    # AIMessage (possibly carrying fully-assembled tool calls — replayed per
    # the gateway contract in tests/gateway/test_messages.py).
    tool_calls = [
        ToolCall(
            id=call.get("id"),
            name=call["name"],
            arguments=dict(call.get("args") or {}),
        )
        for call in (message.tool_calls or [])
    ]
    content = message.content if isinstance(message.content, str) else ""
    return ChatMessage(role="assistant", content=content or None, tool_calls=tool_calls)


def _tool_hops_since_last_human(messages: list[AnyMessage]) -> int:
    """Tool hops used in the current turn (ToolMessages after the last human)."""
    hops = 0
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, ToolMessage):
            hops += 1
    return hops


# --- Nodes -----------------------------------------------------------------


async def _chatbot_node(state: AgentState, client: ChatClient, tools: ToolRegistry, temperature: float) -> dict[str, Any]:
    """One LLM call with the Maya prompt and the tool set; streams tokens."""
    writer = get_stream_writer()
    messages = state["messages"]

    # Loop-proofing: when this turn's tool budget is spent, stop calling the
    # model for tool requests and close the turn with an honest message.
    if _tool_hops_since_last_human(messages) >= MAX_TOOL_HOPS:
        writer(AgentToken(text=TOOL_BUDGET_MESSAGE))
        return {
            "messages": [AIMessage(content=TOOL_BUDGET_MESSAGE)],
            "tool_results": [],
            "proposed_cards": [],
        }

    system_prompt = compile_maya_prompt(
        profile_block=state.get("profile_block") or "",
        cart_summary=state.get("cart_summary") or "",
    )
    wire_messages = [ChatMessage(role="system", content=system_prompt)]
    wire_messages.extend(_to_chat_message(message) for message in messages)

    text_parts: list[str] = []
    requested_calls: list[ToolCall] = []
    usage: dict[str, Any] | None = None
    try:
        async for event in client.stream(
            wire_messages, tools=tools.specs(), temperature=temperature
        ):
            if event.type == "token_delta":
                text_parts.append(event.text)
                writer(AgentToken(text=event.text))
            elif event.type == "tool_call":
                requested_calls.append(event.call)
            elif isinstance(event, FinishEvent):
                usage = event.usage
    except ToolCallDegradationError:
        # Text already yielded stays usable (gateway contract): keep it and
        # surface the honest "can't run the shop tools" message as final text.
        text_so_far = "".join(text_parts)
        notice = f"\n\n{DEGRADATION_MESSAGE}" if text_so_far else DEGRADATION_MESSAGE
        writer(AgentToken(text=notice))
        return {
            "messages": [AIMessage(content=text_so_far + notice)],
            "tool_results": [],
            "proposed_cards": [],
        }

    if usage:
        writer(
            AgentUsage(
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            )
        )

    lc_tool_calls = [
        {
            "name": call.name,
            "args": dict(call.arguments),
            "id": call.id or f"call_{index}",
            "type": "tool_call",
        }
        for index, call in enumerate(requested_calls)
    ]
    content = "".join(text_parts)
    return {
        "messages": [AIMessage(content=content, tool_calls=lc_tool_calls)],
        "tool_results": [],
        # Model card-block claims (3.5): candidates for the guardrail, never
        # for direct rendering. Parsed from this pass's text, so a corrective
        # retry replaces the previous pass's claims wholesale.
        "proposed_cards": parse_card_blocks(content),
    }


async def _tools_node(state: AgentState, tools: ToolRegistry, config: RunnableConfig) -> dict[str, Any]:
    """Execute the model's requested tool calls (one hop) honestly."""
    writer = get_stream_writer()
    last_message = state["messages"][-1]
    # Per-turn identity the model never supplies: cart tools key off the
    # anonymous shopper (user_id) and the chat thread (session_id).
    configurable = config.get("configurable") or {}
    context = {
        "user_id": configurable.get("user_id"),
        "session_id": configurable.get("thread_id"),
    }
    results: list[ToolMessage] = []
    hop_results: list[ToolResult] = []
    hop_cards: list[dict[str, Any]] = []
    comparison: dict[str, Any] | None = None
    cart_summaries: list[dict[str, Any]] = []
    for call in last_message.tool_calls:
        name = call["name"]
        arguments = dict(call.get("args") or {})
        call_id = call.get("id") or name
        writer(AgentToolProgress(name=name, args_summary=summarize_arguments(arguments)))
        started = time.perf_counter()
        output = await tools.execute(name, arguments, context)
        latency_ms = (time.perf_counter() - started) * 1000
        results.append(ToolMessage(content=output.content, tool_call_id=call_id))
        hop_results.append(
            {"name": name, "ok": output.ok, "content": output.content, "latency_ms": latency_ms}
        )
        hop_cards.extend(output.cards)
        if output.comparison is not None:
            comparison = output.comparison
        if output.cart_summary is not None:
            cart_summaries.append(output.cart_summary)
        writer(AgentToolDone(name=name, ok=output.ok, latency_ms=latency_ms))
    # Card payloads attach at tool-execution time (3.3): pending_cards carry
    # what the tools actually returned, across every hop of this turn. Cart
    # summaries likewise accumulate into cart_events — the SSE layer emits
    # the freshest one as the turn's cart_update event (3.7).
    return {
        "messages": results,
        "tool_results": hop_results,
        "pending_cards": list(state.get("pending_cards") or []) + hop_cards,
        "comparison": comparison if comparison is not None else state.get("comparison"),
        "cart_events": list(state.get("cart_events") or []) + cart_summaries,
    }


# --- Guardrail (System Design 3.5) -----------------------------------------


def _hint_for(pending_cards: list[dict[str, Any]], product_id: str) -> str:
    """Reuse the tool-produced match hint when the product was retrieved."""
    for card in pending_cards:
        if card.get("product_id") == product_id:
            return str(card.get("match_hint") or "")
    return ""


def _validate_turn_cards(
    state: AgentState, adapter: CatalogAdapter
) -> tuple[list[dict[str, Any]], list[CardIssue]]:
    """Guardrail pass over one chatbot pass (System Design 3.5).

    Model card claims are the render candidates, in the model's order; each
    must reference an existing catalog product with matching price/rating and
    a schema-shaped payload. When the model emitted no card blocks at all,
    the tool-attached cards render instead (ground truth fallback).

    Returns the accepted card payloads plus every failing claim.
    """
    proposals = state.get("proposed_cards") or []
    pending = state.get("pending_cards") or []
    accepted: list[dict[str, Any]] = []
    failures: list[CardIssue] = []

    if proposals:
        seen: set[str] = set()
        for claim in proposals:
            issue = validate_card(claim, adapter)
            if issue is not None:
                failures.append(issue)
                continue
            product_id = str(claim["product_id"])
            if product_id in seen:
                continue
            seen.add(product_id)
            product = adapter.get_product(product_id)
            if product is None:  # unreachable (validate_card checks); defensive
                continue
            accepted.append(
                card_from_product(product, _hint_for(pending, product_id)).model_dump()
            )
        return accepted, failures

    # No model blocks: fall back to the tool-attached cards (already catalog
    # data; validated defensively so nothing renders un-checked).
    for card in pending:
        product = adapter.get_product(str(card.get("product_id")))
        if product is None:
            continue
        accepted.append(
            card_from_product(product, str(card.get("match_hint") or "")).model_dump()
        )
    return accepted, failures


def _validate_comparison(
    comparison: dict[str, Any] | None, adapter: CatalogAdapter
) -> dict[str, Any] | None:
    """Keep the comparison payload only when every id is catalog-verified."""
    if not comparison:
        return None
    ids = comparison.get("product_ids") or []
    if not ids or not all(adapter.get_product(str(i)) is not None for i in ids):
        return None
    return comparison


async def _validate_node(state: AgentState, adapter: CatalogAdapter) -> dict[str, Any]:
    """Guardrail pass over pending cards (System Design 3.5, PRD FR-8).

    First failure of the turn: append one corrective system message and run
    the chatbot node once more. After the retry (or with no failures), strip
    whatever is still invalid and finalize the turn's cards — the SSE layer
    renders only these validated payloads, never model prose.
    """
    writer = get_stream_writer()
    accepted, failures = _validate_turn_cards(state, adapter)
    comparison = _validate_comparison(state.get("comparison"), adapter)
    prior_failures = list(state.get("stripped_card_ids") or [])
    turn_failures = prior_failures + [issue.product_id for issue in failures]

    if failures and not state.get("retried_validation"):
        failed_ids = sorted({issue.product_id for issue in failures})
        reasons = sorted({issue.reason for issue in failures})
        corrective = CORRECTIVE_TEMPLATE.format(failed=", ".join(failed_ids))
        if reasons != ["unknown_product"]:
            corrective += f" (failures: {', '.join(reasons)})"
        return {
            "messages": [SystemMessage(content=corrective)],
            "stripped_card_ids": turn_failures,
            "retried_validation": True,
        }

    if turn_failures:
        outcome = "retried" if accepted and not failures else "stripped"
    else:
        outcome = "passed"
    writer(
        AgentValidation(
            outcome=outcome,
            failed_product_ids=sorted(set(turn_failures)),
            card_count=len(accepted),
        )
    )
    return {
        "validated_cards": accepted,
        "comparison": comparison,
        "stripped_card_ids": sorted(set(turn_failures)),
        "validation_done": True,
    }


# --- Graph assembly --------------------------------------------------------


def build_agent_graph(
    client: ChatClient,
    tools: ToolRegistry | None = None,
    checkpointer: Any = None,
    temperature: float = DEFAULT_TEMPERATURE,
    adapter: CatalogAdapter | None = None,
) -> Any:
    """Compile the Maya graph.

    ``client`` is injected (composition root / tests) per ADR-001; ``tools``
    defaults to this ticket's registry (placeholder ``get_store_info``).
    ``checkpointer`` (SqliteSaver family) gives one thread per session.
    ``adapter`` is the catalog the guardrail validates against (3.5) and
    defaults to the committed demo catalog.
    """
    registry = tools if tools is not None else tool_registry()
    catalog = adapter if adapter is not None else JsonCatalogAdapter()

    async def chatbot(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        return await _chatbot_node(state, client, registry, temperature)

    async def tools_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        return await _tools_node(state, registry, config)

    async def validate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        return await _validate_node(state, catalog)

    def _route_after_chatbot(state: AgentState) -> str:
        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None):
            return "tools"
        return "validate"

    def _route_after_validate(state: AgentState) -> str:
        # One corrective retry per turn (3.5): back to the chatbot until the
        # verdict is in, then END.
        if state.get("validation_done"):
            return "end"
        return "retry"

    builder = StateGraph(AgentState)
    builder.add_node("chatbot", chatbot)
    builder.add_node("tools", tools_node)
    builder.add_node("validate", validate)
    builder.add_edge(START, "chatbot")
    builder.add_conditional_edges(
        "chatbot", _route_after_chatbot, {"tools": "tools", "validate": "validate"}
    )
    builder.add_edge("tools", "chatbot")
    builder.add_conditional_edges(
        "validate", _route_after_validate, {"retry": "chatbot", "end": END}
    )
    return builder.compile(checkpointer=checkpointer)


# --- Turn execution --------------------------------------------------------


async def stream_agent_turn(
    graph: Any,
    thread_id: str,
    message: str,
    profile_block: str | None = None,
    cart_summary: str | None = None,
    user_id: str | None = None,
) -> AsyncIterator[AgentEvent]:
    """Run one bounded agent turn on ``thread_id``, yielding agent events.

    Custom stream events (tokens, tool progress, the validation verdict) are
    the consumer-facing surface the SSE endpoint maps onto the §3.7 event
    contract. Card/comparison/cart-event state resets here so nothing leaks
    across turns. ``profile_block`` (T07) and ``cart_summary`` (T06) are the
    rendered context blocks the chatbot node injects into the system prompt
    for this turn. ``user_id`` (the anonymous cookie id) rides in the
    configurable context so cart tools can key the shopper's cart — the
    model never supplies an identity.
    """
    configurable: dict[str, Any] = {"thread_id": thread_id}
    if user_id is not None:
        configurable["user_id"] = user_id
    config = {"configurable": configurable}
    inputs: dict[str, Any] = {
        "messages": [HumanMessage(content=message)],
        "tool_results": [],
        "pending_cards": [],
        "proposed_cards": [],
        "validated_cards": [],
        "comparison": None,
        "stripped_card_ids": [],
        "retried_validation": False,
        "validation_done": False,
        "cart_events": [],
        # Always overwrite the channels (even with ""): the checkpointer keeps
        # the previous turn's value otherwise, so a cleared profile or an
        # emptied cart would still be injected on the next turn.
        "profile_block": profile_block or "",
        "cart_summary": cart_summary or "",
    }
    async for event in graph.astream(inputs, config, stream_mode="custom"):
        yield event


# --- Checkpointer (SqliteSaver family, one thread per session) --------------


def checkpointer_path_from_url(url: str) -> Path:
    """Extract the file path from a sqlite URL (e.g. ``sqlite+aiosqlite://``)."""
    for scheme in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(scheme):
            return Path(url[len(scheme):])
    return Path(url)


async def open_checkpointer(db_path: str | Path) -> Any:
    """Open an SQLite checkpointer (async twin of ``SqliteSaver``) at ``path``."""
    connection = await aiosqlite.connect(str(db_path))
    return AsyncSqliteSaver(connection)


async def close_checkpointer(checkpointer: Any) -> None:
    """Close the checkpointer's SQLite connection (app shutdown)."""
    await checkpointer.conn.close()
