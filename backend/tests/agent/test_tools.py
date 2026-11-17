"""Placeholder tool registry for the agent loop (System Design 3.2/3.3).

Seam: ``app.agent.tools`` — the tool set the chatbot node advertises to the
model and the ``tools`` node executes. Catalog/cart tools arrive in T05/T06;
this ticket wires exactly one trivial tool (``get_store_info``) so the
LangGraph loop is real, plus the per-turn hop limit constant.
"""

import json

from app.agent.tools import MAX_TOOL_HOPS, ToolRegistry, tool_registry
from app.gateway.tools import ToolSpec


def test_max_tool_hops_is_six_per_turn():
    assert MAX_TOOL_HOPS == 6


def test_registry_advertises_get_store_info_as_openai_function_schema():
    registry = tool_registry()
    specs = registry.specs()
    assert all(isinstance(spec, ToolSpec) for spec in specs)
    store_info = next(spec for spec in specs if spec.name == "get_store_info")
    wire = store_info.to_wire()
    assert wire["type"] == "function"
    assert wire["function"]["name"] == "get_store_info"
    assert wire["function"]["parameters"]["type"] == "object"
    assert "store" in wire["function"]["description"].lower()


async def test_executing_get_store_info_returns_static_store_blurb():
    registry = tool_registry()
    content, ok = await registry.execute("get_store_info", {})
    assert ok is True
    assert "ShopMate" in content


async def test_executing_unknown_tool_fails_honestly_without_raising():
    registry = tool_registry()
    content, ok = await registry.execute("does_not_exist", {"x": 1})
    assert ok is False
    assert "does_not_exist" in content


def test_summarize_arguments_compacts_arguments_for_sse():
    from app.agent.tools import summarize_arguments

    summary = summarize_arguments({"query": "trail shoes", "limit": 8})
    assert json.loads(summary) == {"query": "trail shoes", "limit": 8}
    assert summarize_arguments({}) == "{}"
