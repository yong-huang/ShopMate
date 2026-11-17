"""Mocked HTTP scaffolding for gateway tests.

Every gateway test drives the real ``ChatClient`` facade over
``httpx.MockTransport``: the provider HTTP boundary is the only mocked seam,
so the tests pin the OpenAI-compatible wire contract and the emitted event
sequence with no network access.
"""

import json
from collections.abc import AsyncIterator
from typing import Any, Callable

import httpx

from app.config import Settings
from app.gateway import OpenAICompatChatClient, create_chat_client

Handler = Callable[[httpx.Request], httpx.Response]


def content_delta(text: str, finish_reason: str | None = None) -> dict[str, Any]:
    """One chat-completions chunk carrying content text (and maybe a finish)."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test-model",
        "choices": [
            {"index": 0, "delta": {"content": text}, "finish_reason": finish_reason}
        ],
    }


def tool_call_delta(
    index: int = 0,
    id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
) -> dict[str, Any]:
    """One chat-completions chunk carrying a tool-call delta fragment."""
    function: dict[str, Any] = {}
    if name is not None:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    entry: dict[str, Any] = {"index": index, "function": function}
    if id is not None:
        entry["id"] = id
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "delta": {"tool_calls": [entry]}, "finish_reason": None}],
    }


def finish_chunk(finish_reason: str = "stop") -> dict[str, Any]:
    """The terminal chat-completions chunk (empty delta, finish reason set)."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
    }


def sse_response(chunks: list[dict[str, Any]], status_code: int = 200) -> httpx.Response:
    """Build a streaming ``text/event-stream`` response replaying ``chunks``."""
    lines = [f"data: {json.dumps(chunk, separators=(',', ':'))}" for chunk in chunks]
    lines.append("data: [DONE]")
    body = ("\n\n".join(lines) + "\n\n").encode("utf-8")

    async def stream() -> AsyncIterator[bytes]:
        yield body

    return httpx.Response(
        status_code,
        content=stream(),
        headers={"content-type": "text/event-stream"},
    )


def make_client(
    settings: Settings, handler: Handler
) -> tuple[OpenAICompatChatClient, list[httpx.Request]]:
    """Build the facade on a mocked transport; return it with captured requests."""
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    transport = httpx.MockTransport(record)
    http_client = httpx.AsyncClient(transport=transport)
    client = create_chat_client(settings, http_client=http_client)
    return client, requests
