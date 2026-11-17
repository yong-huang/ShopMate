"""Provider-switching tests (System Design 3.1 presets, PRD NFR-3).

Seam: ``create_chat_client`` + ``ChatClient.stream`` at the mocked HTTP
boundary. Switching provider is proven to be an env-config-only change: the
same facade code serves glm, openai and ollama, differing only in endpoint,
model and auth — which come from ``Settings``.
"""

import json

import pytest

from app.config import Settings
from app.gateway import (
    ChatMessage,
    FinishEvent,
    GatewayConfigError,
    TokenDeltaEvent,
    create_chat_client,
)
from tests.gateway.mocks import content_delta, make_client, sse_response

SINGLE_TURN = [ChatMessage(role="user", content="hello")]


async def test_openai_provider_streams_through_the_same_facade() -> None:
    settings = Settings(
        _env_file=None, llm_provider="openai", llm_model="gpt-5", llm_api_key="sk-test"
    )
    chunks = [content_delta("Hi there", finish_reason="stop")]
    client, requests = make_client(settings, lambda request: sse_response(chunks))

    events = [event async for event in client.stream(SINGLE_TURN)]

    assert events == [TokenDeltaEvent(text="Hi there"), FinishEvent(finish_reason="stop")]
    request = requests[0]
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-test"
    assert json.loads(request.content)["model"] == "gpt-5"


async def test_ollama_provider_streams_through_the_same_facade_without_api_key() -> None:
    settings = Settings(_env_file=None, llm_provider="ollama")
    chunks = [content_delta("Local hello", finish_reason="stop")]
    client, requests = make_client(settings, lambda request: sse_response(chunks))

    events = [event async for event in client.stream(SINGLE_TURN)]

    assert events == [
        TokenDeltaEvent(text="Local hello"),
        FinishEvent(finish_reason="stop"),
    ]
    request = requests[0]
    assert str(request.url) == "http://localhost:11434/v1/chat/completions"
    assert "authorization" not in request.headers
    assert json.loads(request.content)["model"] == "qwen3:8b"


async def test_custom_llm_base_url_overrides_provider_default() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="glm",
        llm_base_url="https://gateway.internal/api/v4/",
        llm_api_key="test-key",
    )
    client, requests = make_client(
        settings, lambda request: sse_response([content_delta("ok", finish_reason="stop")])
    )

    _events = [event async for event in client.stream(SINGLE_TURN)]

    assert str(requests[0].url) == "https://gateway.internal/api/v4/chat/completions"


async def test_empty_llm_base_url_falls_back_to_provider_default() -> None:
    settings = Settings(_env_file=None, llm_provider="glm", llm_base_url="", llm_api_key="k")
    client, requests = make_client(
        settings, lambda request: sse_response([content_delta("ok", finish_reason="stop")])
    )

    _events = [event async for event in client.stream(SINGLE_TURN)]

    assert str(requests[0].url) == "https://open.bigmodel.cn/api/paas/v4/chat/completions"


async def test_ollama_base_url_override_is_respected() -> None:
    settings = Settings(
        _env_file=None, llm_provider="ollama", ollama_base_url="http://192.168.1.20:11434"
    )
    client, requests = make_client(
        settings, lambda request: sse_response([content_delta("ok", finish_reason="stop")])
    )

    _events = [event async for event in client.stream(SINGLE_TURN)]

    assert str(requests[0].url) == "http://192.168.1.20:11434/v1/chat/completions"


@pytest.mark.parametrize("provider", ["glm", "openai"])
async def test_cloud_provider_without_api_key_is_a_config_error(provider: str) -> None:
    settings = Settings(_env_file=None, llm_provider=provider)

    with pytest.raises(GatewayConfigError):
        create_chat_client(settings)
