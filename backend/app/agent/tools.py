"""Agent tool registry (System Design 3.3).

T04 wired the trivial ``get_store_info`` tool so the LangGraph loop is real;
T05 registers the catalog tools (``search_products``, ``get_product_details``,
``compare_products``) on the same seam. Cart tools belong to T06.

Tools return ``(content, ok)``; failures are honest strings the model can
see, never fabricated results. Catalog executors may return a
:class:`ToolOutput` instead: the same honest pair plus the *structured*
payloads (product cards, comparison matrix) attached at tool-execution
time, so the guardrail validates what the tools actually returned — never
what the model claims (System Design 3.3/3.5).
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
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


@dataclass
class ToolOutput:
    """Result of one tool execution.

    ``content``/``ok`` are what the model sees; ``cards`` and ``comparison``
    are structured payloads for the guardrail + SSE layers (empty/None for
    plain tools). ``cart_summary`` (T06) carries the fresh cart state after a
    cart-affecting tool ran — it becomes the SSE ``cart_update`` event (3.7).
    """

    content: str
    ok: bool
    cards: list[dict[str, Any]] = field(default_factory=list)
    comparison: dict[str, Any] | None = None
    cart_summary: dict[str, Any] | None = None


# ``context`` carries per-turn identity the model never sees: the anonymous
# shopper (``user_id``) and the chat session (``session_id``) the tools node
# passes through from the request. Plain tools ignore it.
Executor = Callable[[dict[str, Any], dict[str, Any] | None], Awaitable[tuple[str, bool] | ToolOutput]]


async def _store_info_executor(
    _arguments: dict[str, Any], _context: dict[str, Any] | None = None
) -> tuple[str, bool]:
    return STORE_INFO_BLURB, True


class ToolRegistry:
    """Tool specs (for the gateway) plus their executors (for the tools node)."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._executors: dict[str, Executor] = {}

    def register(self, spec: ToolSpec, executor: Executor) -> None:
        self._specs[spec.name] = spec
        self._executors[spec.name] = executor

    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    async def execute(
        self, name: str, arguments: dict[str, Any], context: dict[str, Any] | None = None
    ) -> ToolOutput:
        """Run one tool call; unknown tools fail honestly instead of raising."""
        executor = self._executors.get(name)
        if executor is None:
            return ToolOutput(
                content=f"Error: tool '{name}' is not available in this environment.",
                ok=False,
            )
        result = await executor(arguments, context)
        if isinstance(result, ToolOutput):
            return result
        content, ok = result
        return ToolOutput(content=content, ok=ok)


def tool_registry() -> ToolRegistry:
    """The default registry with every tool this ticket provides."""
    registry = ToolRegistry()
    registry.register(STORE_INFO_TOOL, _store_info_executor)
    return registry


def summarize_arguments(arguments: dict[str, Any]) -> str:
    """Compact JSON rendering of tool arguments for the SSE progress event."""
    return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
