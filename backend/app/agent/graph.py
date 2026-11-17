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

from app.agent.prompts import compile_maya_prompt
from app.agent.tools import MAX_TOOL_HOPS, ToolRegistry, summarize_arguments, tool_registry
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
    pending_cards: list[dict[str, Any]]  # ProductCard — T05 fills this
    cart_events: list[dict[str, Any]]  # CartEvent — T06 fills this
    # Rendered shopper profile block (T07, §3.6 injection): set per turn by
    # the chat endpoint from the profile store; the chatbot node renders it
    # into the system prompt's PROFILE_BLOCK seam.
    profile_block: str


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


AgentEvent = Annotated[
    Union[AgentToken, AgentToolProgress, AgentToolDone, AgentUsage],
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
        }

    system_prompt = compile_maya_prompt(
        profile_block=state.get("profile_block") or ""
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
        return {"messages": [AIMessage(content=text_so_far + notice)], "tool_results": []}

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
    }


async def _tools_node(state: AgentState, tools: ToolRegistry) -> dict[str, Any]:
    """Execute the model's requested tool calls (one hop) honestly."""
    writer = get_stream_writer()
    last_message = state["messages"][-1]
    results: list[ToolMessage] = []
    hop_results: list[ToolResult] = []
    for call in last_message.tool_calls:
        name = call["name"]
        arguments = dict(call.get("args") or {})
        call_id = call.get("id") or name
        writer(AgentToolProgress(name=name, args_summary=summarize_arguments(arguments)))
        started = time.perf_counter()
        content, ok = await tools.execute(name, arguments)
        latency_ms = (time.perf_counter() - started) * 1000
        results.append(ToolMessage(content=content, tool_call_id=call_id))
        hop_results.append(
            {"name": name, "ok": ok, "content": content, "latency_ms": latency_ms}
        )
        writer(AgentToolDone(name=name, ok=ok, latency_ms=latency_ms))
    return {"messages": results, "tool_results": hop_results}


async def _validate_node(state: AgentState) -> dict[str, Any]:
    """Guardrail pass over pending cards (System Design 3.5).

    Passthrough in this ticket — T05 plugs validation logic in here. The node
    exists so the graph topology is final and nothing streams as a card
    without passing through it.
    """
    return {}


# --- Graph assembly --------------------------------------------------------


def build_agent_graph(
    client: ChatClient,
    tools: ToolRegistry | None = None,
    checkpointer: Any = None,
    temperature: float = DEFAULT_TEMPERATURE,
) -> Any:
    """Compile the Maya graph.

    ``client`` is injected (composition root / tests) per ADR-001; ``tools``
    defaults to this ticket's registry (placeholder ``get_store_info``).
    ``checkpointer`` (SqliteSaver family) gives one thread per session.
    """
    registry = tools if tools is not None else tool_registry()

    async def chatbot(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        return await _chatbot_node(state, client, registry, temperature)

    async def tools_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        return await _tools_node(state, registry)

    def _route_after_chatbot(state: AgentState) -> str:
        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None):
            return "tools"
        return "validate"

    builder = StateGraph(AgentState)
    builder.add_node("chatbot", chatbot)
    builder.add_node("tools", tools_node)
    builder.add_node("validate", _validate_node)
    builder.add_edge(START, "chatbot")
    builder.add_conditional_edges(
        "chatbot", _route_after_chatbot, {"tools": "tools", "validate": "validate"}
    )
    builder.add_edge("tools", "chatbot")
    builder.add_edge("validate", END)
    return builder.compile(checkpointer=checkpointer)


# --- Turn execution --------------------------------------------------------


async def stream_agent_turn(
    graph: Any,
    thread_id: str,
    message: str,
    profile_block: str | None = None,
) -> AsyncIterator[AgentEvent]:
    """Run one bounded agent turn on ``thread_id``, yielding agent events.

    Custom stream events (tokens, tool progress) are the consumer-facing
    surface the SSE endpoint maps onto the §3.7 event contract.
    ``profile_block`` (T07) is the rendered shopper profile the chatbot node
    injects into the system prompt for this turn.
    """
    config = {"configurable": {"thread_id": thread_id}}
    inputs: dict[str, Any] = {
        "messages": [HumanMessage(content=message)],
        # Always overwrite the channel (even with ""): the checkpointer keeps
        # the previous turn's value otherwise, so a cleared profile would
        # still be injected on the next turn of the same thread.
        "profile_block": profile_block or "",
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
