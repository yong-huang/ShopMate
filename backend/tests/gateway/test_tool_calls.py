"""Tool-calling tests for the model gateway (System Design 3.1 / 3.3).

Seam: ``ChatClient.stream`` with ``tools`` over the mocked HTTP boundary.
Pins the OpenAI function-calling wire schema and the assembly of streamed
tool-call deltas into ``tool_call`` events (before ``finish``).
"""

import json

from app.config import Settings
from app.gateway import (
    ChatMessage,
    FinishEvent,
    TokenDeltaEvent,
    ToolCall,
    ToolCallEvent,
    ToolSpec,
)
from tests.gateway.mocks import (
    content_delta,
    finish_chunk,
    make_client,
    sse_response,
    tool_call_delta,
)

SEARCH_PRODUCTS = ToolSpec(
    name="search_products",
    description="Hybrid catalog search",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["query"],
    },
)


async def test_tool_specs_are_sent_as_openai_function_definitions() -> None:
    settings = Settings(_env_file=None, llm_api_key="test-key")
    client, requests = make_client(
        settings,
        lambda request: sse_response([finish_chunk("stop")]),
    )

    _events = [
        event
        async for event in client.stream(
            [ChatMessage(role="user", content="shoes")], tools=[SEARCH_PRODUCTS]
        )
    ]

    assert json.loads(requests[0].content)["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "search_products",
                "description": "Hybrid catalog search",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
        }
    ]


async def test_streamed_tool_call_deltas_assemble_into_one_tool_call_event() -> None:
    settings = Settings(_env_file=None, llm_api_key="test-key")
    chunks = [
        content_delta("Let me look that up."),
        tool_call_delta(0, id="call_abc", name="search_products", arguments='{"que'),
        tool_call_delta(0, arguments='ry": "trail shoes"}'),
        finish_chunk("tool_calls"),
    ]
    client, _requests = make_client(settings, lambda request: sse_response(chunks))

    events = [event async for event in client.stream([ChatMessage(role="user", content="shoes")])]

    assert events == [
        TokenDeltaEvent(text="Let me look that up."),
        ToolCallEvent(
            call=ToolCall(
                id="call_abc",
                name="search_products",
                arguments={"query": "trail shoes"},
            )
        ),
        FinishEvent(finish_reason="tool_calls"),
    ]


async def test_tool_call_stream_with_no_text_emits_no_token_events() -> None:
    settings = Settings(_env_file=None, llm_api_key="test-key")
    chunks = [
        tool_call_delta(0, id="call_1", name="get_cart", arguments="{}"),
        finish_chunk("tool_calls"),
    ]
    client, _requests = make_client(settings, lambda request: sse_response(chunks))

    events = [event async for event in client.stream([ChatMessage(role="user", content="cart?")])]

    assert events == [
        ToolCallEvent(call=ToolCall(id="call_1", name="get_cart", arguments={})),
        FinishEvent(finish_reason="tool_calls"),
    ]
