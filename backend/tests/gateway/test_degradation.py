"""Degradation tests for the model gateway (System Design 3.1).

Seam: ``ChatClient.stream`` with tools, at the mocked HTTP boundary. When the
configured model repeatedly fails to emit a *valid* tool call — malformed
JSON arguments, missing name, or no tool call at all ("absent") — the Nth
consecutive failed attempt raises ``ToolCallDegradationError`` after yielding
that stream's events, so the agent layer can degrade honestly instead of
hallucinating tool results. Any valid tool call resets the counter.
"""

import pytest

from app.config import Settings
from app.gateway import (
    ChatMessage,
    FinishEvent,
    TokenDeltaEvent,
    ToolCallDegradationError,
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

GET_CART = ToolSpec(
    name="get_cart",
    description="Current cart contents",
    parameters={"type": "object", "properties": {}},
)
MALFORMED_STREAM = [
    tool_call_delta(0, id="call_1", name="get_cart", arguments='{"broken": '),
    finish_chunk("tool_calls"),
]
ABSENT_STREAM = [content_delta("Sure, I can help with shoes!", finish_reason="stop")]
MESSAGE = [ChatMessage(role="user", content="hi")]


async def collect(client: object, tools: list[ToolSpec] | None) -> list[object]:
    events = []
    async for event in client.stream(MESSAGE, tools=tools):
        events.append(event)
    return events


async def test_malformed_tool_call_is_dropped_and_stream_still_completes() -> None:
    settings = Settings(_env_file=None, llm_api_key="test-key")
    client, _requests = make_client(settings, lambda request: sse_response(MALFORMED_STREAM))

    events = await collect(client, tools=[GET_CART])

    assert events == [FinishEvent(finish_reason="tool_calls")]


async def test_degradation_error_after_n_consecutive_failed_attempts() -> None:
    settings = Settings(
        _env_file=None, llm_api_key="test-key", llm_tool_degradation_attempts=2
    )
    client, _requests = make_client(settings, lambda request: sse_response(MALFORMED_STREAM))

    _events = await collect(client, tools=[GET_CART])  # attempt 1: counted, no raise

    with pytest.raises(ToolCallDegradationError) as exc_info:  # attempt 2: threshold
        _events = await collect(client, tools=[GET_CART])

    error = exc_info.value
    assert error.provider == "glm"
    assert error.model == "glm-5.3-flash"
    assert error.failed_attempts == 2
    assert error.attempt_limit == 2
    message = str(error)
    assert "tool" in message
    assert "qwen3:8b" in message  # honest hint per System Design 3.1


async def test_absent_tool_call_counts_toward_degradation() -> None:
    settings = Settings(
        _env_file=None, llm_api_key="test-key", llm_tool_degradation_attempts=1
    )
    client, _requests = make_client(settings, lambda request: sse_response(ABSENT_STREAM))

    with pytest.raises(ToolCallDegradationError) as exc_info:
        _events = await collect(client, tools=[GET_CART])

    assert exc_info.value.failed_attempts == 1


async def test_valid_tool_call_resets_the_failure_counter() -> None:
    settings = Settings(
        _env_file=None, llm_api_key="test-key", llm_tool_degradation_attempts=2
    )
    valid_stream = [
        tool_call_delta(0, id="call_ok", name="get_cart", arguments="{}"),
        finish_chunk("tool_calls"),
    ]
    turn = -1

    def handler(request: object) -> object:
        nonlocal turn
        turn += 1
        return sse_response(MALFORMED_STREAM if turn in (0, 2) else valid_stream)

    client, _requests = make_client(settings, handler)

    _events = await collect(client, tools=[GET_CART])  # attempt 1: failure, count 1
    events = await collect(client, tools=[GET_CART])  # attempt 2: success, reset
    assert any(isinstance(event, ToolCallEvent) for event in events)
    events = await collect(client, tools=[GET_CART])  # attempt 3: count 1 again, no raise
    assert isinstance(events[-1], FinishEvent)


async def test_no_tools_means_no_degradation_counting() -> None:
    settings = Settings(
        _env_file=None, llm_api_key="test-key", llm_tool_degradation_attempts=1
    )
    client, _requests = make_client(settings, lambda request: sse_response(ABSENT_STREAM))

    for _turn in range(3):
        events = await collect(client, tools=None)

        assert events == [
            TokenDeltaEvent(text="Sure, I can help with shoes!"),
            FinishEvent(finish_reason="stop"),
        ]


async def test_non_positive_attempt_limit_disables_degradation() -> None:
    settings = Settings(
        _env_file=None, llm_api_key="test-key", llm_tool_degradation_attempts=0
    )
    client, _requests = make_client(settings, lambda request: sse_response(MALFORMED_STREAM))

    for _turn in range(3):
        events = await collect(client, tools=[GET_CART])

        assert events == [FinishEvent(finish_reason="tool_calls")]
