"""The ChatClient facade over OpenAI-compatible chat completions (System Design 3.1).

One HTTP surface serves all three provider presets (GLM / OpenAI / Ollama);
which one is used is decided entirely by env config (see ``providers.py``).
The client streams SSE chat completions and yields ``ChatEvent`` values:
``token_delta`` | ``tool_call`` | ``finish``.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.config import Settings
from app.gateway.errors import ToolCallDegradationError
from app.gateway.events import ChatEvent, FinishEvent, TokenDeltaEvent, ToolCallEvent
from app.gateway.messages import ChatMessage
from app.gateway.providers import ResolvedEndpoint, resolve_endpoint
from app.gateway.tools import ToolCall, ToolSpec

DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


@dataclass
class _PendingToolCall:
    """Accumulates the streamed fragments of one tool call (keyed by index)."""

    id: str | None = None
    name_parts: list[str] = field(default_factory=list)
    argument_parts: list[str] = field(default_factory=list)


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
        max_failed_tool_call_attempts: int = 3,
    ) -> None:
        self._endpoint = endpoint
        self._http = http_client
        self._max_failed_attempts = max_failed_tool_call_attempts
        self._failed_tool_call_attempts = 0
        self._degraded = False

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
        pending_calls: dict[int, _PendingToolCall] = {}
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
                for call_delta in delta.get("tool_calls") or []:
                    index = call_delta.get("index", 0)
                    pending = pending_calls.setdefault(index, _PendingToolCall())
                    if call_delta.get("id"):
                        pending.id = call_delta["id"]
                    function = call_delta.get("function") or {}
                    if function.get("name"):
                        pending.name_parts.append(function["name"])
                    if function.get("arguments"):
                        pending.argument_parts.append(function["arguments"])
                finish_reason = choices[0].get("finish_reason") or finish_reason
        valid_calls = _assemble_tool_calls(pending_calls)
        for call in valid_calls:
            yield ToolCallEvent(call=call)
        yield FinishEvent(finish_reason=finish_reason)
        if tools:
            self._track_degradation(len(valid_calls) > 0)
            if self._degraded:
                raise ToolCallDegradationError(
                    provider=self._endpoint.provider,
                    model=self._endpoint.model,
                    failed_attempts=self._failed_tool_call_attempts,
                    attempt_limit=self._max_failed_attempts,
                )

    def _track_degradation(self, attempt_succeeded: bool) -> None:
        """Count consecutive failed tool-enabled attempts (reset on success)."""
        if attempt_succeeded:
            self._failed_tool_call_attempts = 0
        else:
            self._failed_tool_call_attempts += 1
        self._degraded = (
            0 < self._max_failed_attempts <= self._failed_tool_call_attempts
        )


def _assemble_tool_calls(pending_calls: dict[int, _PendingToolCall]) -> list[ToolCall]:
    """Join streamed fragments into complete tool calls.

    Calls whose arguments do not parse as a JSON object (or that lack a
    function name) are malformed and dropped here — the degradation tracker
    in ``stream`` counts such attempts so the agent layer can degrade
    honestly instead of executing a hallucinated call.
    """
    valid_calls: list[ToolCall] = []
    for index in sorted(pending_calls):
        pending = pending_calls[index]
        name = "".join(pending.name_parts)
        raw_arguments = "".join(pending.argument_parts) or "{}"
        try:
            arguments = json.loads(raw_arguments)
        except ValueError:
            continue
        if not name or not isinstance(arguments, dict):
            continue
        valid_calls.append(ToolCall(id=pending.id, name=name, arguments=arguments))
    return valid_calls


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
        max_failed_tool_call_attempts=settings.llm_tool_degradation_attempts,
    )
