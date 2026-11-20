"""Agent graph behavior (System Design 3.2).

Seam: ``app.agent.graph`` — ``build_agent_graph`` (the graph factory the app
composition root uses; the ChatClient is injected so tests drive a fake) and
``stream_agent_turn`` (one bounded turn over one session thread, yielding
typed ``AgentEvent`` values). The checkpointer gives one thread per session,
so conversation state survives process restarts.
"""

import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.catalog_tools import register_catalog_tools
from app.agent.graph import (
    DEGRADATION_MESSAGE,
    MAX_TOOL_HOPS,
    TOOL_BUDGET_MESSAGE,
    AgentToolDone,
    AgentToolProgress,
    AgentToken,
    AgentUsage,
    build_agent_graph,
    close_checkpointer,
    open_checkpointer,
    stream_agent_turn,
)
from app.agent.tools import tool_registry
from app.catalog.adapter import JsonCatalogAdapter
from app.gateway.errors import ToolCallDegradationError
from app.gateway.events import (
    FinishEvent,
    TokenDeltaEvent,
    ToolCallEvent,
)
from app.gateway.tools import ToolCall
from app.retrieval.index import RetrievalIndex

from .mocks import ScriptedChatClient

ADAPTER = JsonCatalogAdapter()

# Checkpointers opened during one test; closed afterwards so no aiosqlite
# worker thread outlives the test's event loop.
_OPEN_SAVERS: list = []


@pytest.fixture(autouse=True)
async def _close_checkpointers():
    _OPEN_SAVERS.clear()
    yield
    for saver in _OPEN_SAVERS:
        await close_checkpointer(saver)
    _OPEN_SAVERS.clear()


async def _new_graph(tmp_path, client):
    """A compiled graph over a fresh checkpointer file (auto-closed).

    The registry carries the real catalog tools over a keyword-only index,
    so the tool loop runs against the committed catalog (placeholder
    ``get_store_info`` is retired).
    """
    index = await RetrievalIndex.build(ADAPTER)
    registry = tool_registry()
    register_catalog_tools(registry, ADAPTER, index)
    saver = await open_checkpointer(tmp_path / "ck.db")
    _OPEN_SAVERS.append(saver)
    return build_agent_graph(
        client=client, tools=registry, checkpointer=saver, adapter=ADAPTER
    )


def _text_stream(text: str) -> list:
    return [
        TokenDeltaEvent(text=text),
        FinishEvent(finish_reason="stop"),
    ]


def _tool_call_stream(call_id: str = "call_1", **arguments) -> list:
    return [
        TokenDeltaEvent(text="Let me look that up."),
        ToolCallEvent(
            call=ToolCall(id=call_id, name="search_products", arguments=dict(arguments))
        ),
        FinishEvent(finish_reason="tool_calls"),
    ]


async def test_plain_text_turn_streams_tokens_and_persists_final_message(tmp_path):
    client = ScriptedChatClient([_text_stream("Hello! How can I help?")])
    graph = await _new_graph(tmp_path, client)
    thread_id = uuid.uuid4().hex

    events = [
        event
        async for event in stream_agent_turn(graph, thread_id, "hi, who are you?")
    ]

    tokens = [event for event in events if isinstance(event, AgentToken)]
    assert "".join(token.text for token in tokens) == "Hello! How can I help?"

    state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    messages = state.values["messages"]
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "hi, who are you?"
    assert isinstance(messages[1], AIMessage)
    assert messages[1].content == "Hello! How can I help?"


async def test_chatbot_sends_maya_system_prompt_and_gateway_wire_messages(tmp_path):
    client = ScriptedChatClient([_text_stream("Hi!")])
    graph = await _new_graph(tmp_path, client)

    _ = [event async for event in stream_agent_turn(graph, uuid.uuid4().hex, "hello")]

    wire = client.wire_messages()
    assert wire[0]["role"] == "system"
    assert "Maya" in wire[0]["content"]
    assert wire[1] == {"role": "user", "content": "hello"}
    # The tool set is advertised even when the model chooses not to call.
    assert client.calls[0].tools is not None
    assert any(spec.name == "search_products" for spec in client.calls[0].tools)


async def test_tool_loop_executes_tool_and_feeds_result_back(tmp_path):
    client = ScriptedChatClient(
        [
            _tool_call_stream("call_1", query="wireless headphones"),
            _text_stream("ShopMate sells four categories of products."),
        ]
    )
    graph = await _new_graph(tmp_path, client)
    thread_id = uuid.uuid4().hex

    events = [
        event async for event in stream_agent_turn(graph, thread_id, "what do you sell?")
    ]

    progress = [event for event in events if isinstance(event, AgentToolProgress)]
    assert [(p.name, p.args_summary) for p in progress] == [
        ("search_products", '{"query":"wireless headphones"}')
    ]
    done = [event for event in events if isinstance(event, AgentToolDone)]
    assert len(done) == 1
    assert done[0].ok is True
    assert done[0].latency_ms >= 0

    # Second LLM call replays the assistant tool request + tool result.
    second = client.wire_messages(1)
    assert second[-2] == {
        "role": "assistant",
        "content": "Let me look that up.",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "search_products",
                    "arguments": '{"query": "wireless headphones"}',
                },
            }
        ],
    }
    assert second[-1]["role"] == "tool"
    assert second[-1]["tool_call_id"] == "call_1"
    assert "ELE-001" in second[-1]["content"]

    state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    messages = state.values["messages"]
    assert isinstance(messages[2], ToolMessage)
    assert isinstance(messages[3], AIMessage)
    assert "four categories" in messages[3].content


async def test_tool_hop_limit_stops_execution_after_six_hops(tmp_path):
    # A model stuck in a loop requests the same tool forever; the graph must
    # execute exactly MAX_TOOL_HOPS calls, then close the turn honestly.
    client = ScriptedChatClient(
        [
            _tool_call_stream(f"call_{index}", query="trail shoes")
            for index in range(1, MAX_TOOL_HOPS + 1)
        ]
    )
    graph = await _new_graph(tmp_path, client)
    thread_id = uuid.uuid4().hex

    events = [
        event async for event in stream_agent_turn(graph, thread_id, "find me shoes")
    ]

    progress = [event for event in events if isinstance(event, AgentToolProgress)]
    assert len(progress) == MAX_TOOL_HOPS  # exactly six tool executions
    assert client.exhausted  # the budget short-circuit made no further LLM call

    tokens = [event for event in events if isinstance(event, AgentToken)]
    assert TOOL_BUDGET_MESSAGE in "".join(token.text for token in tokens)

    state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    final_message = state.values["messages"][-1]
    assert isinstance(final_message, AIMessage)
    assert final_message.content == TOOL_BUDGET_MESSAGE
    assert not final_message.tool_calls


async def test_degradation_keeps_streamed_text_and_adds_honest_notice(tmp_path):
    # Text already yielded before the degradation stays usable (gateway
    # contract); the honest "can't run the shop tools" message is appended.
    degraded = ToolCallDegradationError(
        provider="ollama", model="no-tools", failed_attempts=3, attempt_limit=3
    )
    client = ScriptedChatClient([[TokenDeltaEvent(text="Sure, "), degraded]])
    graph = await _new_graph(tmp_path, client)
    thread_id = uuid.uuid4().hex

    events = [event async for event in stream_agent_turn(graph, thread_id, "hi")]

    tokens = [event for event in events if isinstance(event, AgentToken)]
    assert "".join(token.text for token in tokens).startswith("Sure, ")

    state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    final_message = state.values["messages"][-1]
    assert final_message.content == "Sure, \n\n" + DEGRADATION_MESSAGE
    assert not final_message.tool_calls


async def test_degradation_without_text_still_answers_honestly(tmp_path):
    degraded = ToolCallDegradationError(
        provider="ollama", model="no-tools", failed_attempts=3, attempt_limit=3
    )
    client = ScriptedChatClient([[degraded]])
    graph = await _new_graph(tmp_path, client)
    thread_id = uuid.uuid4().hex

    _ = [event async for event in stream_agent_turn(graph, thread_id, "hi")]

    state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    final_message = state.values["messages"][-1]
    assert final_message.content == DEGRADATION_MESSAGE


async def test_conversation_survives_backend_restart(tmp_path):
    # One thread per session: a brand-new graph instance over the same
    # checkpointer DB continues the conversation (checkpointer persistence).
    thread_id = uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}

    saver_1 = await open_checkpointer(tmp_path / "checkpoints.db")
    _OPEN_SAVERS.append(saver_1)
    client_1 = ScriptedChatClient([_text_stream("Hello! How can I help?")])
    graph_1 = build_agent_graph(client=client_1, checkpointer=saver_1)
    _ = [event async for event in stream_agent_turn(graph_1, thread_id, "hi there")]

    saver_2 = await open_checkpointer(tmp_path / "checkpoints.db")
    _OPEN_SAVERS.append(saver_2)
    client_2 = ScriptedChatClient([_text_stream("You said hi, so hello back!")])
    graph_2 = build_agent_graph(client=client_2, checkpointer=saver_2)
    _ = [
        event
        async for event in stream_agent_turn(graph_2, thread_id, "what did I say?")
    ]

    wire = client_2.wire_messages()
    assert any(m["role"] == "user" and m["content"] == "hi there" for m in wire)
    assert any(
        m["role"] == "assistant" and m["content"] == "Hello! How can I help?"
        for m in wire
    )
    state = await graph_2.aget_state(config)
    assert len(state.values["messages"]) == 4


async def test_finish_event_usage_is_surfaced_as_usage_event(tmp_path):
    client = ScriptedChatClient(
        [
            [
                TokenDeltaEvent(text="Hello!"),
                FinishEvent(
                    finish_reason="stop",
                    usage={"prompt_tokens": 120, "completion_tokens": 45},
                ),
            ]
        ]
    )
    graph = await _new_graph(tmp_path, client)

    events = [event async for event in stream_agent_turn(graph, uuid.uuid4().hex, "hi")]

    usage = [event for event in events if isinstance(event, AgentUsage)]
    assert usage == [AgentUsage(prompt_tokens=120, completion_tokens=45)]


async def test_no_usage_event_when_finish_carries_none(tmp_path):
    client = ScriptedChatClient([_text_stream("Hello!")])
    graph = await _new_graph(tmp_path, client)

    events = [event async for event in stream_agent_turn(graph, uuid.uuid4().hex, "hi")]

    assert [event for event in events if isinstance(event, AgentUsage)] == []
