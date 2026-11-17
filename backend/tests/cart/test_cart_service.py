"""Cart service CRUD (System Design 3.3/3.8, PRD FR-4).

Seam: ``app.cart.service`` — the one cart implementation shared by the chat
tools and the REST endpoints. Tests drive it through the public functions
with a real temp SQLite DB and the fixture catalog adapter, so cart state is
observed exactly the way both surfaces read it (via ``cart_summary``).
"""

import pytest
from sqlalchemy import select

from app.cart.service import (
    CartError,
    add_item,
    cart_summary,
    checkout,
    remove_item,
    update_item_quantity,
)
from app.db import CartItem, OrderItem, create_engine_and_sessionmaker, init_db

from .catalog_fixture import (
    PRICE_APPAREL,
    PRICE_HEADPHONES,
    catalog_adapter,
    default_products,
    write_catalog,
)


@pytest.fixture
async def db(tmp_path):
    """An initialized SQLite session factory over a throwaway file."""
    engine, sessionmaker = create_engine_and_sessionmaker(
        f"sqlite+aiosqlite:///{tmp_path}/shopmate.db"
    )
    await init_db(engine)
    yield sessionmaker
    await engine.dispose()


@pytest.fixture
def catalog(tmp_path):
    write_catalog(tmp_path / "catalog", default_products())
    return catalog_adapter(tmp_path / "catalog")


async def test_add_item_creates_cart_with_one_line(db, catalog):
    async with db() as session:
        summary = await add_item(
            session, catalog, "shopper-1", product_id="TST-002", quantity=1
        )
        await session.commit()

    assert summary["total"] == pytest.approx(PRICE_HEADPHONES)
    assert summary["item_count"] == 1
    assert len(summary["items"]) == 1
    item = summary["items"][0]
    assert item["product_id"] == "TST-002"
    assert item["name_en"] == "Aurora X9 Headphones"
    assert item["name_zh"] == "极光 X9 耳机"
    assert item["unit_price"] == pytest.approx(PRICE_HEADPHONES)
    assert item["quantity"] == 1
    assert item["variant"] is None
    assert item["line_total"] == pytest.approx(PRICE_HEADPHONES)


async def test_get_cart_summary_is_empty_before_any_add(db, catalog):
    async with db() as session:
        summary = await cart_summary(session, catalog, "nobody")
    assert summary == {"items": [], "item_count": 0, "total": 0.0, "currency": "USD"}


async def test_adding_same_product_and_variant_merges_into_one_line(db, catalog):
    """AC4.2 idempotency: no phantom duplicate lines for the same variant."""
    async with db() as session:
        await add_item(
            session, catalog, "shopper-1", product_id="TST-001", variant={"size": "M"}
        )
        summary = await add_item(
            session, catalog, "shopper-1", product_id="TST-001", variant={"size": "m"}
        )
        await session.commit()

    assert len(summary["items"]) == 1
    assert summary["items"][0]["quantity"] == 2
    assert summary["items"][0]["variant"] == {"size": "M"}
    assert summary["item_count"] == 2
    assert summary["total"] == pytest.approx(2 * PRICE_APPAREL)


async def test_same_product_with_different_variant_is_a_separate_line(db, catalog):
    async with db() as session:
        await add_item(session, catalog, "s", product_id="TST-001", variant={"size": "M"})
        summary = await add_item(session, catalog, "s", product_id="TST-001", variant={"size": "L"})
        await session.commit()

    assert len(summary["items"]) == 2
    assert summary["total"] == pytest.approx(2 * PRICE_APPAREL)


async def test_update_quantity_resizes_the_line(db, catalog):
    async with db() as session:
        summary = await add_item(session, catalog, "s", product_id="TST-002")
        cart_item_id = summary["items"][0]["cart_item_id"]

        updated = await update_item_quantity(
            session, catalog, "s", cart_item_id, quantity=2
        )
        await session.commit()

    assert updated["items"][0]["quantity"] == 2
    assert updated["total"] == pytest.approx(2 * PRICE_HEADPHONES)


async def test_remove_line_deletes_only_that_line(db, catalog):
    async with db() as session:
        await add_item(session, catalog, "s", product_id="TST-001", variant={"size": "M"})
        summary = await add_item(session, catalog, "s", product_id="TST-002")
        cart_item_id = summary["items"][1]["cart_item_id"]

        remaining = await remove_item(session, catalog, "s", cart_item_id)
        await session.commit()

    assert [item["product_id"] for item in remaining["items"]] == ["TST-001"]
    assert remaining["total"] == pytest.approx(PRICE_APPAREL)


async def test_remove_unknown_cart_item_is_an_error(db, catalog):
    async with db() as session:
        with pytest.raises(CartError) as excinfo:
            await remove_item(session, catalog, "s", cart_item_id=999)
    assert excinfo.value.code == "cart_item_not_found"


async def test_update_unknown_cart_item_is_an_error(db, catalog):
    async with db() as session:
        with pytest.raises(CartError) as excinfo:
            await update_item_quantity(session, catalog, "s", 999, quantity=1)
    assert excinfo.value.code == "cart_item_not_found"


async def test_quantity_zero_is_rejected(db, catalog):
    async with db() as session:
        summary = await add_item(session, catalog, "s", product_id="TST-002")
        cart_item_id = summary["items"][0]["cart_item_id"]
        with pytest.raises(CartError) as excinfo:
            await update_item_quantity(session, catalog, "s", cart_item_id, quantity=0)
    assert excinfo.value.code == "invalid_quantity"


async def test_unknown_product_is_rejected(db, catalog):
    async with db() as session:
        with pytest.raises(CartError) as excinfo:
            await add_item(session, catalog, "s", product_id="NOPE-1")
    assert excinfo.value.code == "product_not_found"


async def test_quantity_beyond_demo_stock_is_rejected(db, catalog):
    """3.3: add_to_cart reports out-of-demo-stock instead of silently capping."""
    async with db() as session:
        with pytest.raises(CartError) as excinfo:
            await add_item(session, catalog, "s", product_id="TST-002", quantity=3)
    assert excinfo.value.code == "out_of_stock"


async def test_merged_quantity_beyond_stock_is_rejected(db, catalog):
    async with db() as session:
        await add_item(session, catalog, "s", product_id="TST-002")
        with pytest.raises(CartError) as excinfo:
            await add_item(session, catalog, "s", product_id="TST-002", quantity=2)
    assert excinfo.value.code == "out_of_stock"


async def test_variant_must_be_size_or_color(db, catalog):
    async with db() as session:
        with pytest.raises(CartError) as excinfo:
            await add_item(
                session, catalog, "s", product_id="TST-001", variant={"width": "wide"}
            )
    assert excinfo.value.code == "invalid_variant"


async def test_size_must_be_one_the_catalog_defines(db, catalog):
    async with db() as session:
        with pytest.raises(CartError) as excinfo:
            await add_item(
                session, catalog, "s", product_id="TST-001", variant={"size": "XXL"}
            )
    assert excinfo.value.code == "invalid_variant"


async def test_product_without_sizes_rejects_size_requests(db, catalog):
    async with db() as session:
        with pytest.raises(CartError) as excinfo:
            await add_item(
                session, catalog, "s", product_id="TST-002", variant={"size": "M"}
            )
    assert excinfo.value.code == "invalid_variant"


async def test_color_must_match_the_catalog_colour(db, catalog):
    async with db() as session:
        with pytest.raises(CartError) as excinfo:
            await add_item(
                session, catalog, "s", product_id="TST-002", variant={"color": "Snow White"}
            )
    assert excinfo.value.code == "invalid_variant"

    summary = await add_item(
        session, catalog, "s", product_id="TST-002", variant={"color": "midnight black"}
    )
    assert summary["items"][0]["variant"] == {"color": "Midnight Black"}


# --- Checkout (System Design 3.8 snapshot semantics, PRD FR-5) --------------


async def test_checkout_refuses_an_empty_cart(db, catalog):
    async with db() as session:
        with pytest.raises(CartError) as excinfo:
            await checkout(session, catalog, "s")
    assert excinfo.value.code == "empty_cart"


async def test_checkout_persists_order_and_snapshots_catalog_prices(db, catalog):
    async with db() as session:
        await add_item(session, catalog, "s", product_id="TST-001", variant={"size": "M"})
        await add_item(session, catalog, "s", product_id="TST-002", quantity=2)

        order = await checkout(session, catalog, "s", session_id="sess-1")
        await session.commit()

    assert order["status"] == "demo_paid"
    assert order["currency"] == "USD"
    assert order["total"] == pytest.approx(PRICE_APPAREL + 2 * PRICE_HEADPHONES)
    assert len(order["items"]) == 2
    jacket, headphones = order["items"]
    assert jacket["product_id"] == "TST-001"
    assert jacket["name"] == "Trailblazer Rain Jacket"
    assert jacket["unit_price"] == pytest.approx(PRICE_APPAREL)
    assert jacket["quantity"] == 1
    assert jacket["variant"] == {"size": "M"}
    assert headphones["unit_price"] == pytest.approx(PRICE_HEADPHONES)
    assert order["order_id"]

    # Order rows persisted with the name/price snapshots at purchase time.
    async with db() as session:
        rows = (await session.execute(select(OrderItem))).scalars().all()
    assert {row.product_id for row in rows} == {"TST-001", "TST-002"}
    assert {row.name_snapshot for row in rows} == {
        "Trailblazer Rain Jacket",
        "Aurora X9 Headphones",
    }
    by_product = {row.product_id: row for row in rows}
    assert by_product["TST-002"].unit_price == pytest.approx(PRICE_HEADPHONES)
    assert by_product["TST-001"].variant == {"size": "M"}


async def test_catalog_mutation_does_not_rewrite_order_history(db, catalog, tmp_path):
    """Snapshot semantics: prices are frozen at purchase time (3.8)."""
    async with db() as session:
        await add_item(session, catalog, "s", product_id="TST-002")
        order = await checkout(session, catalog, "s")
        await session.commit()

    # The catalog price changes after the purchase…
    products = default_products()
    for product in products:
        if product["id"] == "TST-002":
            product["price"] = 999.99
    write_catalog(tmp_path / "catalog", products)
    changed_catalog = catalog_adapter(tmp_path / "catalog")
    assert changed_catalog.get_product("TST-002").price == 999.99

    # …but the persisted order keeps the purchase-time snapshot.
    async with db() as session:
        rows = (await session.execute(select(OrderItem))).scalars().all()
    assert rows[0].unit_price == pytest.approx(PRICE_HEADPHONES)
    assert rows[0].order_id == order["order_id"]


async def test_checkout_clears_the_cart(db, catalog):
    async with db() as session:
        await add_item(session, catalog, "s", product_id="TST-002")
        await checkout(session, catalog, "s")

        summary = await cart_summary(session, catalog, "s")
        await session.commit()

    assert summary["items"] == []
    assert summary["total"] == 0.0
    async with db() as session:
        assert (await session.execute(select(CartItem))).scalars().all() == []


async def test_checkout_records_purchase_interactions(db, catalog):
    """T06 x T07 seam: purchases flow into the profile interaction log."""
    from app.db import get_profile

    async with db() as session:
        await add_item(session, catalog, "s", product_id="TST-002", session_id="sess-9")
        await checkout(session, catalog, "s", session_id="sess-9")
        await session.commit()

        profile = await get_profile(session, "s")
    types = [event["type"] for event in profile.interaction_log]
    assert types == ["add_to_cart", "purchase"]
    purchase = profile.interaction_log[-1]
    assert purchase["product_id"] == "TST-002"
    assert purchase["session_id"] == "sess-9"


async def test_add_records_add_to_cart_interaction(db, catalog):
    from app.db import get_profile

    async with db() as session:
        await add_item(
            session,
            catalog,
            "s",
            product_id="TST-001",
            variant={"size": "M"},
            session_id="sess-7",
        )
        await session.commit()

        profile = await get_profile(session, "s")
    event = profile.interaction_log[-1]
    assert event["type"] == "add_to_cart"
    assert event["product_id"] == "TST-001"
    assert event["session_id"] == "sess-7"
    assert event["quantity"] == 1
