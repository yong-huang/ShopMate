"""SSE product_card / comparison events + guardrail turn-log audit (3.5/3.7).

Seam: the HTTP boundary — the real FastAPI app with the ChatClient faked and
the real committed catalog behind the tools. Cards on the wire must come from
validated tool-verified payloads only; fabricated model references must never
produce a product_card event, and their ids must land in the chat_turn
validation record (pre-strip audit) for the eval harness.
"""

import uuid

from sqlalchemy import select

from app.catalog.adapter import JsonCatalogAdapter
from app.db import Message
from app.gateway.events import FinishEvent, TokenDeltaEvent, ToolCallEvent
from app.gateway.tools import ToolCall

from .agent.mocks import ScriptedChatClient
from .test_chat_api import app_factory, parse_sse, stream_chat  # noqa: F401

ADAPTER = JsonCatalogAdapter()


def _claim(product_id: str) -> dict:
    product = ADAPTER.get_product(product_id)
    return {"product_id": product_id, "price": product.price, "rating": product.rating}


def _card_block(claim: dict) -> str:
    return (
        "```product-card\n"
        f'{{"product_id": "{claim["product_id"]}", "price": {claim["price"]}, '
        f'"rating": {claim["rating"]}}}\n'
        "```"
    )


def _search_script(query: str) -> list:
    return [
        TokenDeltaEvent(text="Looking for options."),
        ToolCallEvent(
            call=ToolCall(id="c1", name="search_products", arguments={"query": query})
        ),
        FinishEvent(finish_reason="tool_calls"),
    ]


def _text_script(text: str) -> list:
    return [TokenDeltaEvent(text=text), FinishEvent(finish_reason="stop")]


async def test_guidance_turn_emits_validated_product_card_events(app_factory):
    fake_blocks = "My picks:\n" + _card_block(_claim("ELE-001")) + _card_block(_claim("ELE-002"))
    fake = ScriptedChatClient(
        [
            _search_script("noise cancelling headphones for travel"),
            _text_script(fake_blocks),
        ]
    )
    app = await app_factory(fake)
    session_id = uuid.uuid4().hex

    response = await stream_chat(app, {"session_id": session_id, "message": "travel headphones"})

    events = parse_sse(response.text)
    names = [event["event"] for event in events]
    assert names[0] == "session"
    assert names[-1] == "done"
    card_events = [event for event in events if event["event"] == "product_card"]
    assert [event["data"]["product_id"] for event in card_events] == ["ELE-001", "ELE-002"]
    # Card payloads are catalog ground truth (never the model's prose).
    for event in card_events:
        product = ADAPTER.get_product(event["data"]["product_id"])
        assert event["data"]["price"] == product.price
        assert event["data"]["rating"] == product.rating
        assert event["data"]["currency"] == "USD"
        assert event["data"]["image_url"] == f"/static/catalog/images/{product.id}.svg"
        assert event["data"]["name_zh"]
    # Cards come after all tokens, before done (3.7 wire order).
    assert names.index("product_card") > max(
        index for index, name in enumerate(names) if name == "token"
    )


async def test_fabricated_card_never_reaches_the_stream_and_is_audited(
    app_factory, json_lines
):
    fake = ScriptedChatClient(
        [
            _text_script(
                "Great deal:\n" + _card_block({"product_id": "FAKE-999", "price": 9.99, "rating": 5}),
            ),
            _text_script("I could not verify that product in the catalog, sorry."),
        ]
    )
    app = await app_factory(fake)
    session_id = uuid.uuid4().hex

    response = await stream_chat(app, {"session_id": session_id, "message": "cheap thing X"})

    events = parse_sse(response.text)
    assert not [event for event in events if event["event"] == "product_card"]
    assert events[-1]["event"] == "done"

    # Pre-strip audit in the chat_turn record for the eval harness.
    turns = json_lines.of_event("chat_turn")
    assert len(turns) == 1
    validation = turns[0]["validation"]
    assert validation["outcome"] == "stripped"
    assert "FAKE-999" in validation["failed_product_ids"]

    # The corrective system message is persisted with the turn's messages.
    async with app.state.sessionmaker() as db:
        rows = (
            (await db.execute(select(Message).where(Message.session_id == session_id)))
            .scalars()
            .all()
        )
    roles = [row.role for row in rows]
    assert "system" in roles
    assert any("FAKE-999" in (row.content or "") for row in rows if row.role == "system")


async def test_comparison_event_carries_matrix_and_recommendation(app_factory):
    compare = [
        TokenDeltaEvent(text="Comparing."),
        ToolCallEvent(
            call=ToolCall(
                id="c1", name="compare_products", arguments={"product_ids": ["ELE-001", "ELE-002"]}
            )
        ),
        FinishEvent(finish_reason="tool_calls"),
    ]
    fake = ScriptedChatClient([compare, _text_script("Choose ELE-001 for battery life.")])
    app = await app_factory(fake)

    response = await stream_chat(app, {"session_id": uuid.uuid4().hex, "message": "compare ELE-001 and ELE-002"})

    events = parse_sse(response.text)
    comparisons = [event for event in events if event["event"] == "comparison"]
    assert len(comparisons) == 1
    data = comparisons[0]["data"]
    assert data["product_ids"] == ["ELE-001", "ELE-002"]
    assert data["matrix"]["columns"] == ["ELE-001", "ELE-002"]
    assert {row["label"] for row in data["matrix"]["rows"]} >= {"Price", "Rating"}
    assert data["recommendation"] == "Choose ELE-001 for battery life."


async def test_plain_turn_logs_passed_validation_without_cards(app_factory, json_lines):
    fake = ScriptedChatClient([_text_script("Hello!")])
    app = await app_factory(fake)

    await stream_chat(app, {"session_id": uuid.uuid4().hex, "message": "hi"})

    turns = json_lines.of_event("chat_turn")
    assert turns[0]["validation"]["outcome"] == "passed"
    assert turns[0]["validation"]["failed_product_ids"] == []
