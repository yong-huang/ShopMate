"""The ChatClient facade over OpenAI-compatible chat completions (System Design 3.1).

One HTTP surface serves all three provider presets (GLM / OpenAI / Ollama);
which one is used is decided entirely by env config (see ``providers.py``).
The client streams SSE chat completions and yields ``ChatEvent`` values:
``token_delta`` | ``tool_call`` | ``finish``.
"""

import json
from collections.abc import AsyncIterator
from typing import Protocol

import httpx

from app.config import Settings
from app.gateway.events import ChatEvent, FinishEvent, TokenDeltaEvent
from app.gateway.messages import ChatMessage
from app.gateway.providers import ResolvedEndpoint, resolve_endpoint
from app.gateway.tools import ToolSpec

DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


class ChatClient(Protocol):
    """The one interface that hides the provider (ADR-001, System Design 3.1)."""

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
    ) -> AsyncIterator[ChatEvent]:
        """Stream one chat completion as ``ChatEvent`` values."""
        ...


class OpenAICompatChatClient(ChatClient):
    """Streams chat completions (with tool calling) over OpenAI-compatible SSE."""

    def __init__(
        self,
        *,
        endpoint: ResolvedEndpoint,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._endpoint = endpoint
        self._http = http_client

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
    ) -> AsyncIterator[ChatEvent]:
        body: dict[str, object] = {
            "model": self._endpoint.model,
            "messages": [message.to_wire() for message in messages],
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            body["tools"] = [tool.to_wire() for tool in tools]
        headers = {"Accept": "text/event-stream"}
        if self._endpoint.api_key:
            headers["Authorization"] = f"Bearer {self._endpoint.api_key}"

        finish_reason: str | None = None
        async with self._http.stream(
            "POST", self._endpoint.chat_url, headers=headers, json=body
        ) as response:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue  # blank lines, SSE comments, other fields
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                if not payload:
                    continue
                chunk = json.loads(payload)
                choices = chunk.get("choices") or []
                if not choices:
                    continue  # e.g. usage-only trailing chunk
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    yield TokenDeltaEvent(text=text)
                finish_reason = choices[0].get("finish_reason") or finish_reason
        yield FinishEvent(finish_reason=finish_reason)


def create_chat_client(
    settings: Settings, *, http_client: httpx.AsyncClient | None = None
) -> OpenAICompatChatClient:
    """Build the facade for the configured provider.

    ``http_client`` is injectable so tests (and callers with special TLS or
    proxy needs) can supply their own transport; by default a client with
    conservative timeouts is created.
    """
    endpoint = resolve_endpoint(settings)  # may raise GatewayConfigError
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    return OpenAICompatChatClient(
        endpoint=endpoint,
        http_client=http_client,
    )
