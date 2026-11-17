"""Product cards: schema, model-block parsing, guardrail validation (3.5/3.7).

Seam: ``app.agent.cards`` — the card payload schema the SSE layer emits, the
``product-card`` block format Maya uses to reference products in her reply,
and the validator that gates every card against the real catalog before it
may render. These tests use the committed catalog JSON (deterministic).
"""

import pytest

from app.catalog.adapter import JsonCatalogAdapter
from app.catalog.schemas import ProductDetail

from app.agent.cards import (
    ProductCardPayload,
    card_from_product,
    parse_card_blocks,
    validate_card,
)

ADAPTER = JsonCatalogAdapter()
HEADPHONES = ADAPTER.get_product("ELE-001")
JACKET = ADAPTER.get_product("APP-001")


def test_card_from_product_matches_sse_schema_with_static_image_url():
    card = card_from_product(HEADPHONES, match_hint="Matched tags for 'travel'")
    assert card.model_dump() == {
        "product_id": "ELE-001",
        "name_en": "Aurora X9 Wireless Noise-Cancelling Headphones",
        "name_zh": "极光 X9 无线降噪耳机",
        "price": 249.99,
        "currency": "USD",
        "rating": 4.7,
        "image_url": "/static/catalog/images/ELE-001.svg",
        "match_hint": "Matched tags for 'travel'",
    }


def test_parse_card_blocks_extracts_maya_card_references_in_order():
    text = (
        "Here are two picks:\n"
        "```product-card\n"
        '{"product_id": "ELE-001", "price": 249.99, "rating": 4.7}\n'
        "```\n"
        "and a budget option:\n"
        "```product-card\n"
        '{"product_id": "APP-001", "price": 189.0, "rating": 4.6}\n'
        "```\n"
        "Enjoy!"
    )
    blocks = parse_card_blocks(text)
    assert blocks == [
        {"product_id": "ELE-001", "price": 249.99, "rating": 4.7},
        {"product_id": "APP-001", "price": 189.0, "rating": 4.6},
    ]


def test_parse_card_blocks_ignores_other_fences_and_malformed_json():
    text = (
        "```python\nprint('nope')\n```\n"
        "```product-card\nnot json at all\n```\n"
        "```product-card\n[]\n```\n"
        "plain text with no blocks"
    )
    assert parse_card_blocks(text) == []


def test_parse_card_blocks_returns_empty_when_no_blocks():
    assert parse_card_blocks("Just prose, no card blocks.") == []


def test_card_payload_schema_rejects_missing_or_extra_fields():
    import pydantic

    full = {
        "product_id": "ELE-001",
        "name_en": "n",
        "name_zh": "n",
        "price": 1.0,
        "currency": "USD",
        "rating": 4.5,
        "image_url": "/static/catalog/images/ELE-001.svg",
        "match_hint": "",
    }
    assert ProductCardPayload(**full).product_id == "ELE-001"
    for missing in ("product_id", "name_en", "price", "rating", "image_url"):
        broken = {key: value for key, value in full.items() if key != missing}
        with pytest.raises(pydantic.ValidationError):
            ProductCardPayload(**broken)


def test_validate_card_accepts_a_truthful_claim():
    claim = {"product_id": "ELE-001", "price": 249.99, "rating": 4.7}
    assert validate_card(claim, ADAPTER) is None


def test_validate_card_rejects_fabricated_product_id():
    claim = {"product_id": "FAKE-999", "price": 49.99, "rating": 4.9}
    issue = validate_card(claim, ADAPTER)
    assert issue is not None
    assert issue.reason == "unknown_product"
    assert issue.product_id == "FAKE-999"


def test_validate_card_rejects_price_mismatch_against_catalog():
    claim = {"product_id": "ELE-001", "price": 199.99, "rating": 4.7}
    issue = validate_card(claim, ADAPTER)
    assert issue is not None
    assert issue.reason == "price_mismatch"


def test_validate_card_rejects_rating_mismatch_against_catalog():
    claim = {"product_id": "ELE-001", "price": 249.99, "rating": 5.0}
    issue = validate_card(claim, ADAPTER)
    assert issue is not None
    assert issue.reason == "rating_mismatch"


def test_validate_card_rejects_schema_violations():
    # Missing required fields.
    assert validate_card({"product_id": "ELE-001"}, ADAPTER) is not None
    # Unknown extra field.
    assert (
        validate_card(
            {"product_id": "ELE-001", "price": 249.99, "rating": 4.7, "brand": "Aurora"},
            ADAPTER,
        ) is not None
    )
    # Wrong types.
    assert (
        validate_card({"product_id": "ELE-001", "price": "cheap", "rating": 4.7}, ADAPTER)
        is not None
    )
    # Not an object at all.
    assert validate_card(["ELE-001"], ADAPTER) is not None


def test_validate_card_reason_for_schema_violation_is_named():
    issue = validate_card({"product_id": "ELE-001"}, ADAPTER)
    assert issue is not None
    assert issue.reason == "schema"
