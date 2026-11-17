"""Tool domain models for the model gateway (System Design 3.3).

Schemas are OpenAI function-calling JSON Schema dicts: ``ToolSpec.parameters``
is passed through verbatim, so the agent layer declares tools once and every
provider receives the same schema.
"""

import json
from typing import Any

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    """One LLM-callable tool, declared as an OpenAI function schema."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        """Map to the OpenAI chat-completions ``tools`` entry."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolCall(BaseModel):
    """One assembled tool call requested by the model."""

    id: str | None = None
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        """Map to an assistant message ``tool_calls`` entry (arguments JSON-encoded)."""
        call: dict[str, Any] = {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": json.dumps(self.arguments)},
        }
        return {key: value for key, value in call.items() if value is not None}
