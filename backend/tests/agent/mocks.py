"""Mocked ChatClient for agent-graph tests.

The gateway boundary is the only mocked seam: ``ScriptedChatClient`` is a fake
implementation of the ``ChatClient`` Protocol (T03, ADR-001) whose streams are
scripted per call. It records every ``stream`` invocation (messages, tools,
temperature) so tests can assert exactly what the agent sent over the wire.
"""

from dataclasses import dataclass, field
from typing import Any

from app.gateway.events import ChatEvent
from app.gateway.messages import ChatMessage
from app.gateway.tools import ToolSpec


@dataclass
class StreamCall:
    """One recorded ``client.stream`` invocation."""

    messages: list[ChatMessage] = field(default_factory=list)
    tools: list[ToolSpec] | None = None
    temperature: float = 0.3


class ScriptedChatClient:
    """ChatClient fake that replays a scripted event list per stream call."""

    def __init__(
        self, streams: list[list[ChatEvent] | BaseException]
    ) -> None:
        self._streams = list(streams)
        self.calls: list[StreamCall] = []

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
    ):
        call = StreamCall(messages=list(messages), tools=tools, temperature=temperature)
        self.calls.append(call)
        script = self._streams.pop(0)
        if isinstance(script, BaseException):
            raise script
        for event in script:
            if isinstance(event, BaseException):
                # Mirrors the real client: events already yielded stay valid,
                # then the stream ends with a raise (e.g. degradation).
                raise event
            yield event

    @property
    def exhausted(self) -> bool:
        return not self._streams

    def wire_messages(self, call_index: int = -1) -> list[dict[str, Any]]:
        """The wire-format messages of one recorded call (default: last)."""
        return [message.to_wire() for message in self.calls[call_index].messages]
