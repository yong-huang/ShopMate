"""Conversation message model for the model gateway (System Design 3.1).

The agent layer builds these from its own conversation state; the gateway
serializes them to the OpenAI-compatible wire format.
"""

from typing import Any, Literal

from pydantic import BaseModel

from app.gateway.tools import ToolCall

Role = Literal["system", "user", "assistant", "tool"]


class ChatMessage(BaseModel):
    """One conversation message in OpenAI chat-completions shape."""

    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def to_wire(self) -> dict[str, Any]:
        """Serialize to the chat-completions ``messages`` entry."""
        message: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            message["content"] = self.content
        if self.tool_calls:
            message["tool_calls"] = [call.to_wire() for call in self.tool_calls]
        if self.tool_call_id is not None:
            message["tool_call_id"] = self.tool_call_id
        return message
