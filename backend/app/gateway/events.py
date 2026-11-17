"""Streaming event model for the model gateway (System Design 3.1).

``ChatClient.stream`` yields exactly three kinds of events, discriminated by
``type``: ``token_delta`` | ``tool_call`` | ``finish``.
"""

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from app.gateway.tools import ToolCall


class TokenDeltaEvent(BaseModel):
    """A piece of assistant text as it streams in."""

    type: Literal["token_delta"] = "token_delta"
    text: str


class ToolCallEvent(BaseModel):
    """One fully assembled, valid tool call requested by the model.

    Emitted only after the stream completes, once the arguments accumulated
    across deltas parse as JSON; malformed tool calls are never emitted.
    """

    type: Literal["tool_call"] = "tool_call"
    call: ToolCall


class FinishEvent(BaseModel):
    """Stream completion, carrying the provider's finish reason verbatim.

    ``usage`` optionally carries the provider's token-usage payload (when the
    provider emits a trailing usage chunk); the agent layer records it into
    the turn log when present.
    """

    type: Literal["finish"] = "finish"
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None


ChatEvent = Annotated[
    Union[TokenDeltaEvent, ToolCallEvent, FinishEvent],
    Field(discriminator="type"),
]
