"""Cart/checkout tools on the ToolRegistry (System Design 3.3, PRD FR-4/FR-5).

Seam: ``ToolRegistry.execute`` — the same boundary the LangGraph tools node
drives. Tests script no LLM here; they call the executors directly with the
context dict the graph provides (``user_id``/``session_id``), a real temp
SQLite DB, and the fixture catalog, asserting the honest ``(content, ok)``
contract and the structured ``cart_summary`` payload that becomes the SSE
``cart_update`` event.
"""

import json

import pytest

from app.agent.tools import ToolRegistry
from app.cart.tools import (
    ADD_TO_CART_TOOL,
    CART_TOOL_NAMES,
    CHECKOUT_TOOL,
    GET_CART_TOOL,
    REMOVE_FROM_CART_TOOL,
    register_cart_tools,
)
from app.db import create_engine_and_sessionmaker, init_db

from ..cart.catalog_fixture import (
    PRICE_HEADPHONES,
    catalog_adapter,
    default_products,
    write_catalog,
)

CONTEXT = {"user_id": "shopper-1", "session_id": "sess-1"}


def _content_dict(output) -> dict:
    return json.loads(output.content)


@pytest.fixture
async def cart_env(tmp_path):
    """(registry, sessionmaker) over one throwaway DB + fixture catalog."""
    engine, sessionmaker = create_engine_and_sessionmaker(
        f"sqlite+aiosqlite:///{tmp_path}/shopmate.db"
    )
    await init_db(engine)
    write_catalog(tmp_path / "catalog", default_products())
    registry = ToolRegistry()
    register_cart_tools(registry, sessionmaker, catalog_adapter(tmp_path / "catalog"))
    yield registry, sessionmaker
    await engine.dispose()


@pytest.fixture
async def registry(cart_env):
    return cart_env[0]


async def test_register_cart_tools_adds_exactly_the_four_cart_tools():
    registry = ToolRegistry()
    # Registration needs a sessionmaker + catalog; dummies suffice for the
    # schema-level assertion (executors are covered by the other tests).
    register_cart_tools(registry, sessionmaker=None, catalog=None)
    assert [spec.name for spec in registry.specs()] == list(CART_TOOL_NAMES)


async def test_cart_tool_schemas_match_the_design_spec():
    assert ADD_TO_CART_TOOL.parameters["required"] == ["product_id"]
    props = ADD_TO_CART_TOOL.parameters["properties"]
    assert set(props) == {"product_id", "quantity", "variant"}
    assert set(props["variant"]["properties"]) == {"size", "color"}
    assert GET_CART_TOOL.parameters["properties"] == {}
    assert REMOVE_FROM_CART_TOOL.parameters["required"] == ["cart_item_id"]
    assert set(CHECKOUT_TOOL.parameters["properties"]) == {"shipping_note"}


async def test_add_to_cart_returns_summary_and_persists_the_line(registry, tmp_path):
    output = await registry.execute(
        "add_to_cart",
        {"product_id": "TST-002", "quantity": 1},
        context=CONTEXT,
    )

    assert output.ok is True
    payload = _content_dict(output)
    assert payload["total"] == pytest.approx(PRICE_HEADPHONES)
    assert output.cart_summary == payload

    # The line persists for the same shopper (get_cart sees it).
    view = await registry.execute("get_cart", {}, context=CONTEXT)
    assert view.ok is True
    assert _content_dict(view)["items"][0]["product_id"] == "TST-002"


async def test_add_to_cart_without_shopper_context_fails_honestly(registry):
    output = await registry.execute("add_to_cart", {"product_id": "TST-002"})
    assert output.ok is False
    assert "shopper" in output.content.lower()
    assert output.cart_summary is None


async def test_add_to_cart_reports_unknown_product_honestly(registry):
    output = await registry.execute(
        "add_to_cart", {"product_id": "NOPE-1"}, context=CONTEXT
    )
    assert output.ok is False
    assert "NOPE-1" in output.content


async def test_add_to_cart_reports_variant_errors_honestly(registry):
    output = await registry.execute(
        "add_to_cart",
        {"product_id": "TST-001", "variant": {"size": "XXL"}},
        context=CONTEXT,
    )
    assert output.ok is False
    assert "size" in output.content.lower()


async def test_remove_from_cart_removes_the_line(registry):
    await registry.execute("add_to_cart", {"product_id": "TST-002"}, context=CONTEXT)
    view = await registry.execute("get_cart", {}, context=CONTEXT)
    cart_item_id = _content_dict(view)["items"][0]["cart_item_id"]

    output = await registry.execute(
        "remove_from_cart", {"cart_item_id": cart_item_id}, context=CONTEXT
    )

    assert output.ok is True
    assert _content_dict(output)["items"] == []


async def test_remove_from_cart_unknown_id_is_an_honest_error(registry):
    output = await registry.execute(
        "remove_from_cart", {"cart_item_id": 4242}, context=CONTEXT
    )
    assert output.ok is False
    assert "4242" in output.content


async def test_checkout_refuses_empty_cart_honestly(registry):
    output = await registry.execute("checkout", {}, context=CONTEXT)
    assert output.ok is False
    assert "empty" in output.content.lower()
    assert output.cart_summary is None


async def test_checkout_places_demo_order_and_clears_cart(registry):
    await registry.execute("add_to_cart", {"product_id": "TST-002"}, context=CONTEXT)

    output = await registry.execute("checkout", {}, context=CONTEXT)

    assert output.ok is True
    payload = _content_dict(output)
    assert payload["order_id"]
    assert payload["status"] == "demo_paid"
    assert payload["total"] == pytest.approx(PRICE_HEADPHONES)
    # 3.3: the tool result carries the demo disclaimer the model relays.
    assert "demo" in output.content.lower()
    # The cart is cleared; the summary payload reflects the emptied cart.
    assert output.cart_summary["items"] == []
    view = await registry.execute("get_cart", {}, context=CONTEXT)
    assert _content_dict(view)["item_count"] == 0


async def test_cart_tools_use_the_context_identity_per_shopper(registry):
    """Carts are per anonymous shopper — context isolates two shoppers."""
    await registry.execute(
        "add_to_cart", {"product_id": "TST-002"}, context=CONTEXT
    )
    other = {"user_id": "shopper-2", "session_id": "sess-2"}
    view = await registry.execute("get_cart", {}, context=other)
    assert _content_dict(view)["item_count"] == 0


async def test_cart_add_records_interaction_with_session(cart_env):
    from sqlalchemy import select

    from app.db import Profile

    registry, sessionmaker = cart_env
    await registry.execute("add_to_cart", {"product_id": "TST-002"}, context=CONTEXT)

    async with sessionmaker() as db:
        profile = (await db.execute(select(Profile))).scalars().one()
    event = profile.interaction_log[-1]
    assert event["type"] == "add_to_cart"
    assert event["product_id"] == "TST-002"
    assert event["session_id"] == "sess-1"
