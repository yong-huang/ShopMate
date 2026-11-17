"""Cart flows through POST /api/chat (System Design 3.7, PRD FR-4/FR-5).

Seam: the HTTP chat boundary with the ChatClient faked at the gateway.
Tests script Maya turns that call the real registered cart tools against the
committed catalog, then assert the SSE ``cart_update`` contract, cart/order
persistence for the cookie identity, profile interaction capture, and the
cart summary injection into Maya's system prompt.
"""

import uuid

import pytest
from sqlalchemy import select

from app.db import CartItem, Order, OrderItem, Profile
from app.gateway.events import FinishEvent, TokenDeltaEvent, ToolCallEvent
from app.gateway.tools import ToolCall

from .agent.mocks import ScriptedChatClient
from .test_chat_api import app_factory, parse_sse, stream_chat  # noqa: F401

SESSION_COOKIE = "shopmate_sid"
SHOPPER = {"shopmate_sid": "shopper-cart-0001"}
AURORA_PRICE = 249.99
AURORA_NAME = "Aurora X9 Wireless Noise-Cancelling Headphones"


def _add_then(*extra_turns):
    """Script an add-to-cart turn followed by ``extra_turns``."""
    streams = [
        [
            TokenDeltaEvent(text="Adding the Aurora X9 to your cart."),
            ToolCallEvent(
                call=ToolCall(
                    id="c1",
                    name="add_to_cart",
                    arguments={"product_id": "ELE-001", "quantity": 1},
                )
            ),
            FinishEvent(finish_reason="tool_calls"),
        ],
        [
            TokenDeltaEvent(text="Added! Your cart has 1 item."),
            FinishEvent(finish_reason="stop"),
        ],
    ]
    streams.extend(extra_turns)
    return ScriptedChatClient(streams)


def _checkout_turns():
    return [
        [
            TokenDeltaEvent(text="Placing your demo order."),
            ToolCallEvent(call=ToolCall(id="c2", name="checkout", arguments={})),
            FinishEvent(finish_reason="tool_calls"),
        ],
        [
            TokenDeltaEvent(text="Order placed — this is a demo, no payment."),
            FinishEvent(finish_reason="stop"),
        ],
    ]


async def test_add_to_cart_turn_emits_cart_update_and_persists_cart(app_factory):
    app = await app_factory(_add_then())
    session_id = uuid.uuid4().hex

    response = await stream_chat(
        app,
        {"session_id": session_id, "message": "add the Aurora X9 headphones"},
        client=SHOPPER,
    )

    assert response.status_code == 200
    events = parse_sse(response.text)
    names = [event["event"] for event in events]
    assert names == [
        "session",
        "token",
        "tool_call",
        "token",
        "cart_update",
        "done",
    ]
    update = events[names.index("cart_update")]["data"]
    assert update["item_count"] == 1
    assert update["total"] == pytest.approx(AURORA_PRICE)
    assert [item["product_id"] for item in update["items"]] == ["ELE-001"]

    # Same cart state the storefront GET /api/cart would read.
    async with app.state.sessionmaker() as db:
        rows = (await db.execute(select(CartItem))).scalars().all()
    assert len(rows) == 1
    assert rows[0].cart_id == SHOPPER[SESSION_COOKIE]
    assert rows[0].product_id == "ELE-001"


async def test_cart_summary_is_injected_into_the_next_turn_prompt(app_factory):
    fake = _add_then(
        [TokenDeltaEvent(text="Anything else?"), FinishEvent(finish_reason="stop")]
    )
    app = await app_factory(fake)
    session_id = uuid.uuid4().hex

    await stream_chat(
        app, {"session_id": session_id, "message": "add the Aurora X9"}, client=SHOPPER
    )
    await stream_chat(
        app, {"session_id": session_id, "message": "what's in my cart?"}, client=SHOPPER
    )

    # The cart block is compiled at turn start, so turn 2's system prompt
    # (the last recorded call) carries the cart state after turn 1's add.
    wire = fake.wire_messages(-1)
    system = next(message for message in wire if message["role"] == "system")
    assert "## Cart summary" in system["content"]
    assert AURORA_NAME in system["content"]
    assert "$249.99" in system["content"]


async def test_checkout_turn_persists_order_and_clears_cart(app_factory):
    fake = _add_then(*_checkout_turns())
    app = await app_factory(fake)
    session_id = uuid.uuid4().hex

    await stream_chat(
        app, {"session_id": session_id, "message": "add the Aurora X9"}, client=SHOPPER
    )
    response = await stream_chat(
        app, {"session_id": session_id, "message": "check out"}, client=SHOPPER
    )

    events = parse_sse(response.text)
    names = [event["event"] for event in events]
    assert "cart_update" in names
    update = events[names.index("cart_update")]["data"]
    assert update["items"] == []
    assert update["total"] == 0.0

    async with app.state.sessionmaker() as db:
        order = (await db.execute(select(Order))).scalars().one()
        items = (await db.execute(select(OrderItem))).scalars().all()
        cart_rows = (await db.execute(select(CartItem))).scalars().all()
    assert order.user_id == SHOPPER[SESSION_COOKIE]
    assert order.status == "demo_paid"
    assert order.total == pytest.approx(AURORA_PRICE)
    assert len(items) == 1
    assert items[0].name_snapshot == AURORA_NAME
    assert items[0].unit_price == pytest.approx(AURORA_PRICE)
    assert cart_rows == []


async def test_checkout_on_empty_cart_is_refused_without_an_order(app_factory):
    fake = ScriptedChatClient(
        [
            [
                TokenDeltaEvent(text="Checking."),
                ToolCallEvent(call=ToolCall(id="c1", name="checkout", arguments={})),
                FinishEvent(finish_reason="tool_calls"),
            ],
            [
                TokenDeltaEvent(text="Your cart is empty, so nothing to check out."),
                FinishEvent(finish_reason="stop"),
            ],
        ]
    )
    app = await app_factory(fake)

    response = await stream_chat(
        app, {"session_id": uuid.uuid4().hex, "message": "check out"}, client=SHOPPER
    )

    events = parse_sse(response.text)
    assert [event["event"] for event in events][-1] == "done"
    assert all(event["event"] != "cart_update" for event in events)
    async with app.state.sessionmaker() as db:
        assert (await db.execute(select(Order))).scalars().all() == []


async def test_cart_turns_record_profile_interactions(app_factory):
    fake = _add_then(*_checkout_turns())
    app = await app_factory(fake)
    session_id = uuid.uuid4().hex

    await stream_chat(
        app, {"session_id": session_id, "message": "add the Aurora X9"}, client=SHOPPER
    )
    await stream_chat(
        app, {"session_id": session_id, "message": "check out"}, client=SHOPPER
    )

    async with app.state.sessionmaker() as db:
        profile = await db.get(Profile, SHOPPER[SESSION_COOKIE])
    types = [event["type"] for event in profile.interaction_log]
    assert types == ["add_to_cart", "purchase"]
    assert all(event["session_id"] == session_id for event in profile.interaction_log)
