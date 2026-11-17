"""Error-boundary tests for the model gateway.

Seam: ``ChatClient.stream`` against a mocked transport that misbehaves at the
protocol level. The gateway surfaces provider failures as typed errors with
diagnostics (status, body snippet) instead of swallowing them.
"""

import json

import httpx
import pytest

from app.config import Settings
from app.gateway import ChatMessage, ProviderHTTPError, ProviderResponseError
from tests.gateway.mocks import content_delta, make_client, sse_response

MESSAGE = [ChatMessage(role="user", content="hello")]


async def test_http_error_status_raises_provider_http_error_with_diagnostics() -> None:
    settings = Settings(_env_file=None, llm_api_key="bad-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            content=json.dumps({"error": {"message": "invalid api key"}}).encode(),
            headers={"content-type": "application/json"},
        )

    client, _requests = make_client(settings, handler)

    with pytest.raises(ProviderHTTPError) as exc_info:
        _events = [event async for event in client.stream(MESSAGE)]

    error = exc_info.value
    assert error.provider == "glm"
    assert error.status_code == 401
    assert "invalid api key" in error.body_snippet
    assert "401" in str(error)


async def test_unparseable_sse_chunk_raises_provider_response_error() -> None:
    settings = Settings(_env_file=None, llm_api_key="test-key")
    broken_line = "data: {not-json"
    good_line = f"data: {json.dumps(content_delta('hello'), separators=(',', ':'))}"
    body = f"{good_line}\n\n{broken_line}\n\ndata: [DONE]\n\n".encode()

    async def stream() -> object:
        yield body

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=stream(),
            headers={"content-type": "text/event-stream"},
        )

    client, _requests = make_client(settings, handler)

    with pytest.raises(ProviderResponseError) as exc_info:
        _events = [event async for event in client.stream(MESSAGE)]

    assert exc_info.value.provider == "glm"
    assert "not-json" in str(exc_info.value)
