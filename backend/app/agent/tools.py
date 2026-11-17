"""Agent tool registry (System Design 3.3).

This ticket (T04) wires exactly one trivial tool — ``get_store_info`` — so
the LangGraph chatbot/tools loop is real. Catalog tools (``search_products``,
``get_product_details``) and cart tools belong to T05/T06 and register
themselves here. Tools return ``(content, ok)``; failures are honest strings
the model can see, never fabricated results.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.gateway.tools import ToolSpec

MAX_TOOL_HOPS = 6  # bounded tool hops per turn (System Design 3.2)

STORE_INFO_TOOL = ToolSpec(
    name="get_store_info",
    description="Return general information about the ShopMate store "
    "(what it sells, shipping, returns). Use for store-level questions.",
    parameters={"type": "object", "properties": {}, "required": []},
)

STORE_INFO_BLURB = (
    "ShopMate is a demo store with four categories: Electronics, "
    "Home & Kitchen, Apparel & Accessories, and Sports & Outdoors "
    "(150 products). Free shipping over $50; 30-day returns on everything."
)

Executor = Callable[[dict[str, Any]], Awaitable[tuple[str, bool]]]


async def _store_info_executor(_arguments: dict[str, Any]) -> tuple[str, bool]:
    return STORE_INFO_BLURB, True


class ToolRegistry:
    """Tool specs (for the gateway) plus their executors (for the tools node)."""

    def __init__(self) -> None:
        self._executors: dict[str, Executor] = {}

    def register(self, spec: ToolSpec, executor: Executor) -> None:
        self._executors[spec.name] = executor

    def specs(self) -> list[ToolSpec]:
        return [
            spec
            for spec in (STORE_INFO_TOOL,)
            if spec.name in self._executors
        ]

    async def execute(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Run one tool call; unknown tools fail honestly instead of raising."""
        executor = self._executors.get(name)
        if executor is None:
            return (
                f"Error: tool '{name}' is not available in this environment.",
                False,
            )
        return await executor(arguments)


def tool_registry() -> ToolRegistry:
    """The default registry with every tool this ticket provides."""
    registry = ToolRegistry()
    registry.register(STORE_INFO_TOOL, _store_info_executor)
    return registry


def summarize_arguments(arguments: dict[str, Any]) -> str:
    """Compact JSON rendering of tool arguments for the SSE progress event."""
    return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
