"""Agent tool registry (System Design 3.2/3.3).

Seam: ``app.agent.tools`` — the tool set the chatbot node advertises to the
model and the ``tools`` node executes. T05 adds the catalog tools
(``search_products``, ``get_product_details``, ``compare_products``); cart
tools belong to T06. Executors return a ``ToolOutput``: the honest
``(content, ok)`` pair the model sees plus the structured payloads (product
cards, comparison matrices) attached at tool-execution time.
"""

def _noop_executor(_arguments, _context=None) -> None:
    return None

import json

from app.agent.tools import MAX_TOOL_HOPS, ToolRegistry, ToolOutput, tool_registry
from app.gateway.tools import ToolSpec


def test_max_tool_hops_is_six_per_turn():
    assert MAX_TOOL_HOPS == 6


def test_base_registry_is_empty_placeholder_retired():
    """The T04 placeholder ``get_store_info`` is retired; the base registry
    stays empty and the §3.3 seven tools register at composition time."""
    assert tool_registry().specs() == []


async def test_executing_unregistered_placeholder_fails_honestly():
    registry = tool_registry()
    output = await registry.execute("get_store_info", {})
    assert isinstance(output, ToolOutput)
    assert output.ok is False
    assert "get_store_info" in output.content


async def test_executing_unknown_tool_fails_honestly_without_raising():
    registry = tool_registry()
    output = await registry.execute("does_not_exist", {"x": 1})
    assert output.ok is False
    assert "does_not_exist" in output.content


def test_registry_advertises_every_registered_spec_in_order():
    """Registered tools appear in specs() so the model can call them (T05)."""
    registry = ToolRegistry()
    first = ToolSpec(name="tool_a", description="A", parameters={"type": "object"})
    second = ToolSpec(name="tool_b", description="B", parameters={"type": "object"})
    registry.register(first, _noop_executor)
    registry.register(second, _noop_executor)

    assert [spec.name for spec in registry.specs()] == ["tool_a", "tool_b"]


async def test_executor_returning_tool_output_keeps_structured_payloads():
    """Catalog executors attach cards/comparison alongside (content, ok)."""
    registry = ToolRegistry()
    spec = ToolSpec(name="structured", description="S", parameters={"type": "object"})

    async def executor(_arguments, _context=None):
        return ToolOutput(content="found", ok=True, cards=[{"product_id": "X"}])

    registry.register(spec, executor)
    output = await registry.execute("structured", {})
    assert output.content == "found"
    assert output.cards == [{"product_id": "X"}]


async def test_executor_returning_plain_tuple_is_normalized_to_tool_output():
    registry = ToolRegistry()
    spec = ToolSpec(name="plain", description="P", parameters={"type": "object"})

    async def executor(_arguments, _context=None):
        return "plain result", True

    registry.register(spec, executor)
    output = await registry.execute("plain", {})
    assert output == ToolOutput(content="plain result", ok=True)


def test_summarize_arguments_compacts_arguments_for_sse():
    from app.agent.tools import summarize_arguments

    summary = summarize_arguments({"query": "trail shoes", "limit": 8})
    assert json.loads(summary) == {"query": "trail shoes", "limit": 8}
    assert summarize_arguments({}) == "{}"
