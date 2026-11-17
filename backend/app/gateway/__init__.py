"""Model gateway: hides the configured LLM provider behind one interface.

Public surface (System Design 3.1):

- ``ChatClient`` protocol and the OpenAI-compatible implementation
  (``create_chat_client`` builds it from typed env settings).
- ``ChatEvent`` stream: ``token_delta`` | ``tool_call`` | ``finish``.
- ``ChatMessage`` / ``ToolSpec`` / ``ToolCall`` conversation and tool models
  with OpenAI-compatible wire mapping.
- Typed errors, including ``ToolCallDegradationError`` for honest degradation
  when the configured model cannot emit valid tool calls.
"""

from app.gateway.client import ChatClient, OpenAICompatChatClient, create_chat_client
from app.gateway.errors import (
    GatewayConfigError,
    GatewayError,
    ProviderHTTPError,
    ToolCallDegradationError,
)
from app.gateway.events import (
    ChatEvent,
    FinishEvent,
    TokenDeltaEvent,
    ToolCallEvent,
)
from app.gateway.messages import ChatMessage
from app.gateway.providers import ResolvedEndpoint, resolve_endpoint
from app.gateway.tools import ToolCall, ToolSpec

__all__ = [
    "ChatClient",
    "ChatEvent",
    "ChatMessage",
    "FinishEvent",
    "GatewayConfigError",
    "GatewayError",
    "OpenAICompatChatClient",
    "ProviderHTTPError",
    "ResolvedEndpoint",
    "TokenDeltaEvent",
    "ToolCall",
    "ToolCallDegradationError",
    "ToolCallEvent",
    "ToolSpec",
    "create_chat_client",
    "resolve_endpoint",
]
