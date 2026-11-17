"""Facade streaming tests for the model gateway (System Design 3.1).

Seam: ``ChatClient.stream`` observed at the provider HTTP boundary (mocked).
These pin the OpenAI-compatible wire contract and the emitted ChatEvent
sequence; degradation and tool-call behavior get their own modules.
"""

import json

from app.config import Settings
from app.gateway import ChatMessage, FinishEvent, TokenDeltaEvent
from tests.gateway.mocks import content_delta, make_client, sse_response


async def test_stream_yields_token_deltas_then_finish() -> None:
    settings = Settings(_env_file=None, llm_api_key="test-key-glm")
    chunks = [content_delta("Hello"), content_delta(" world", finish_reason="stop")]
    client, _requests = make_client(settings, lambda request: sse_response(chunks))

    events = [event async for event in client.stream([ChatMessage(role="user", content="Hi")])]

    assert events == [
        TokenDeltaEvent(text="Hello"),
        TokenDeltaEvent(text=" world"),
        FinishEvent(finish_reason="stop"),
    ]


async def test_stream_posts_openai_compatible_request() -> None:
    settings = Settings(_env_file=None, llm_model="glm-5.3-flash", llm_api_key="secret-glm")
    client, requests = make_client(
        settings, lambda request: sse_response([content_delta("Hey", finish_reason="stop")])
    )

    _events = [
        event
        async for event in client.stream([ChatMessage(role="user", content="Hi")], temperature=0.7)
    ]

    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert request.headers["authorization"] == "Bearer secret-glm"
    assert request.headers["accept"] == "text/event-stream"
    assert json.loads(request.content) == {
        "model": "glm-5.3-flash",
        "messages": [{"role": "user", "content": "Hi"}],
        "temperature": 0.7,
        "stream": True,
    }
