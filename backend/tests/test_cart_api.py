"""Storefront cart REST endpoints (System Design 3.7, PRD FR-4/FR-5).

Seam: the HTTP boundary — ``GET /api/cart``, ``POST /api/cart/items``,
``PATCH``/``DELETE /api/cart/items/{id}``, ``POST /api/checkout``. The same
shared cart service backs these and the chat tools, so tests assert REST
parity with the chat path plus the ``{code, message}`` error shape and the
anonymous cookie identity.
"""

import uuid

import httpx
import pytest
from sqlalchemy import select

from app.db import CartItem, Order, OrderItem

from .test_chat_api import stream_chat

SESSION_COOKIE = "shopmate_sid"
SHOPPER = {SESSION_COOKIE: "shopper-rest-0001"}
AURORA_PRICE = 249.99


async def cart_request(app, method, path, json=None, client=SHOPPER):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=client
    ) as http:
        return await http.request(method, path, json=json)


async def test_get_cart_is_empty_for_a_new_shopper(app_factory):
    app = await app_factory()

    response = await cart_request(app, "GET", "/api/cart")

    assert response.status_code == 200
    body = response.json()
    assert body == {"items": [], "item_count": 0, "total": 0.0, "currency": "USD"}


async def test_get_cart_issues_the_identity_cookie_on_first_contact(app_factory):
    app = await app_factory()

    response = await cart_request(app, "GET", "/api/cart", client={})

    set_cookie = response.headers["set-cookie"]
    assert f"{SESSION_COOKIE}=" in set_cookie
    assert "HttpOnly" in set_cookie


async def test_post_cart_items_adds_a_line(app_factory):
    app = await app_factory()

    response = await cart_request(
        app,
        "POST",
        "/api/cart/items",
        json={"product_id": "ELE-001", "quantity": 2},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["item_count"] == 2
    assert body["total"] == pytest.approx(2 * AURORA_PRICE)
    assert body["items"][0]["product_id"] == "ELE-001"


async def test_post_cart_items_accepts_variants(app_factory):
    app = await app_factory()

    response = await cart_request(
        app,
        "POST",
        "/api/cart/items",
        json={"product_id": "APP-001", "quantity": 1, "variant": {"size": "M"}},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["variant"] == {"size": "M"}


async def test_post_cart_items_rejects_unknown_product_with_error_shape(app_factory):
    app = await app_factory()

    response = await cart_request(
        app, "POST", "/api/cart/items", json={"product_id": "NOPE-1"}
    )

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "product_not_found"
    assert "NOPE-1" in body["message"]


async def test_post_cart_items_rejects_invalid_variant(app_factory):
    app = await app_factory()

    response = await cart_request(
        app,
        "POST",
        "/api/cart/items",
        json={"product_id": "APP-001", "variant": {"size": "QQQ"}},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_variant"


async def test_patch_updates_quantity(app_factory):
    app = await app_factory()
    added = (
        await cart_request(app, "POST", "/api/cart/items", json={"product_id": "ELE-001"})
    ).json()
    cart_item_id = added["items"][0]["cart_item_id"]

    response = await cart_request(
        app, "PATCH", f"/api/cart/items/{cart_item_id}", json={"quantity": 3}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["quantity"] == 3
    assert body["total"] == pytest.approx(3 * AURORA_PRICE)


async def test_patch_unknown_item_is_404(app_factory):
    app = await app_factory()

    response = await cart_request(
        app, "PATCH", "/api/cart/items/999", json={"quantity": 1}
    )

    assert response.status_code == 404
    assert response.json()["code"] == "cart_item_not_found"


async def test_delete_removes_the_line(app_factory):
    app = await app_factory()
    added = (
        await cart_request(app, "POST", "/api/cart/items", json={"product_id": "ELE-001"})
    ).json()
    cart_item_id = added["items"][0]["cart_item_id"]

    response = await cart_request(app, "DELETE", f"/api/cart/items/{cart_item_id}")

    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_checkout_from_the_cart_page_places_a_demo_order(app_factory):
    app = await app_factory()
    await cart_request(app, "POST", "/api/cart/items", json={"product_id": "ELE-001"})

    response = await cart_request(app, "POST", "/api/checkout", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["order_id"]
    assert body["status"] == "demo_paid"
    assert body["total"] == pytest.approx(AURORA_PRICE)
    assert body["items"][0]["name"] == "Aurora X9 Wireless Noise-Cancelling Headphones"
    assert body["items"][0]["unit_price"] == pytest.approx(AURORA_PRICE)
    assert "demo" in body["demo_notice"].lower()

    # Persisted order with purchase-time snapshots; cart cleared.
    async with app.state.sessionmaker() as db:
        order = (await db.execute(select(Order))).scalars().one()
        items = (await db.execute(select(OrderItem))).scalars().all()
        cart_rows = (await db.execute(select(CartItem))).scalars().all()
    assert order.user_id == SHOPPER[SESSION_COOKIE]
    assert items[0].unit_price == pytest.approx(AURORA_PRICE)
    assert cart_rows == []


async def test_checkout_refuses_an_empty_cart(app_factory):
    app = await app_factory()

    response = await cart_request(app, "POST", "/api/checkout", json={})

    assert response.status_code == 409
    assert response.json()["code"] == "empty_cart"


async def test_storefront_cart_matches_the_chat_cart_for_one_shopper(app_factory):
    """REST parity (FR-4): chat adds, the storefront cart API reads the same rows."""
    from app.gateway.events import FinishEvent, TokenDeltaEvent, ToolCallEvent
    from app.gateway.tools import ToolCall

    from .agent.mocks import ScriptedChatClient

    fake = ScriptedChatClient(
        [
            [
                TokenDeltaEvent(text="Adding."),
                ToolCallEvent(
                    call=ToolCall(
                        id="c1",
                        name="add_to_cart",
                        arguments={"product_id": "ELE-001", "quantity": 1},
                    )
                ),
                FinishEvent(finish_reason="tool_calls"),
            ],
            [TokenDeltaEvent(text="Added."), FinishEvent(finish_reason="stop")],
        ]
    )
    app = await app_factory(fake)
    await stream_chat(
        app,
        {"session_id": uuid.uuid4().hex, "message": "add the Aurora X9"},
        client=SHOPPER,
    )

    response = await cart_request(app, "GET", "/api/cart")

    assert response.status_code == 200
    body = response.json()
    assert [item["product_id"] for item in body["items"]] == ["ELE-001"]
    assert body["total"] == pytest.approx(AURORA_PRICE)
