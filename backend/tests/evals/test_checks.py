"""Hermetic unit tests for the rule-check engine (Eval Plan §3.1)."""

import pytest
from app.catalog.adapter import JsonCatalogAdapter
from evals.runner.checks import (
    GroundingAudit,
    TurnObservation,
    actual_cart_lines,
    check_cart,
    check_cards,
    check_clarifying,
    check_format,
    check_grounding,
    check_language,
    check_must_not_mention,
    check_pre_strip,
    check_stream,
    check_tools,
    check_turn,
    detect_language,
)
from evals.schema import Turn, TurnExpect
from tests.cart.catalog_fixture import _product, write_catalog

CATALOG_PRODUCTS = [
    _product(
        id="ELE-001",
        name_en="Aurora X9 Headphones",
        name_zh="极光 X9 耳机",
        category="Electronics",
        price=249.99,
        rating=4.7,
    ),
    _product(
        id="HOM-002",
        name_en="EverHeat Kettle",
        name_zh="恒温电水壶",
        category="Home & Kitchen",
        price=79.99,
        rating=4.7,
    ),
    _product(
        id="APP-001",
        name_en="Rain Jacket",
        name_zh="雨衣",
        category="Apparel & Accessories",
        price=189.0,
        rating=4.7,
        sizing={"sizes": ["S", "M", "L"]},
    ),
]


@pytest.fixture
def catalog(tmp_path):
    directory = tmp_path / "catalog"
    write_catalog(directory, CATALOG_PRODUCTS)
    return JsonCatalogAdapter(catalog_dir=directory)


def card(product_id: str, price: float = 10.0, rating: float = 4.5) -> dict:
    return {
        "product_id": product_id,
        "name_en": "x",
        "name_zh": "x",
        "price": price,
        "currency": "USD",
        "rating": rating,
        "image_url": f"/static/catalog/images/{product_id}.svg",
        "match_hint": "",
    }


# --- language heuristic ------------------------------------------------------


def test_detect_language_cjk_ratio_matches_production_threshold():
    assert detect_language("你好，我想买耳机") == "zh"
    assert detect_language("I want noise cancelling headphones under $200") == "en"
    assert detect_language("") is None
    assert detect_language("   ") is None


# --- grounding ---------------------------------------------------------------


def test_grounding_flags_fabricated_rendered_card(catalog):
    result, audit = check_grounding([card("ELE-001"), card("XXX-999")], catalog)
    assert result.ok is False
    assert audit.fabricated_rendered == ["XXX-999"]
    assert audit.hard_fail is True


def test_grounding_rate_counts_rendered_and_pre_strip(catalog):
    audit = GroundingAudit(
        rendered_card_ids=["ELE-001", "ELE-001"],
        fabricated_rendered=[],
        pre_strip_ids=["XXX-999"],
        fabricated_pre_strip=["XXX-999"],
    )
    # 2 rendered valid + 1 catalog-existing pre-strip id / 3 total.
    assert audit.grounding_rate == pytest.approx(2 / 3)
    assert audit.hard_fail is False
    assert GroundingAudit().grounding_rate is None


def test_pre_strip_audit_absorbed_fabrication_fails(catalog):
    result, audit = check_pre_strip(
        {"outcome": "stripped", "failed_product_ids": ["XXX-999"]}, catalog
    )
    assert result.ok is False
    assert audit.fabricated_pre_strip == ["XXX-999"]


def test_pre_strip_audit_price_drift_is_not_fabrication(catalog):
    # A stripped id that exists (price/rating drift) is inaccuracy, not a
    # fabricated product id — no grounding failure.
    result, audit = check_pre_strip(
        {"outcome": "retried", "failed_product_ids": ["ELE-001"]}, catalog
    )
    assert result.ok is True
    assert audit.fabricated_pre_strip == []
    assert audit.pre_strip_ids == ["ELE-001"]


def test_merge_audits():
    merged = GroundingAudit(rendered_card_ids=["A"]).merge(
        GroundingAudit(pre_strip_ids=["B"], fabricated_pre_strip=["B"])
    )
    assert merged.rendered_card_ids == ["A"]
    assert merged.fabricated_pre_strip == ["B"]


# --- tools -------------------------------------------------------------------


def test_tools_subset_is_order_insensitive():
    expect = TurnExpect(expected_tools_subset=["search_products", "get_cart"])
    assert check_tools(expect, ["get_cart", "search_products"]).ok is True
    assert check_tools(expect, ["search_products"]).ok is False


def test_tools_excluded():
    expect = TurnExpect(expected_tools_excluded=["checkout"])
    assert check_tools(expect, ["get_cart"]).ok is True
    result = check_tools(expect, ["get_cart", "checkout"])
    assert result.ok is False and "checkout" in result.detail


def test_tools_none_when_no_expectations():
    assert check_tools(TurnExpect(), ["checkout"]) is None


# --- cards -------------------------------------------------------------------


def test_cards_min_max_and_price_band(catalog):
    expect = TurnExpect(min_cards=1, max_cards=2, card_price_min=100, card_price_max=300)
    result = check_cards(expect, [card("ELE-001", price=249.99)], catalog)
    assert result is not None and result.ok is True
    too_many = check_cards(expect, [card("ELE-001"), card("HOM-002"), card("APP-001")], catalog)
    assert too_many.ok is False and "max 2" in too_many.detail
    cheap = check_cards(expect, [card("ELE-001", price=10)], catalog)
    assert cheap.ok is False and "min 100" in cheap.detail


def test_cards_category_any_of(catalog):
    expect = TurnExpect(card_category_any_of=["Home & Kitchen"])
    assert check_cards(expect, [card("HOM-002")], catalog).ok is True
    result = check_cards(expect, [card("ELE-001")], catalog)
    assert result.ok is False and "Home & Kitchen" in result.detail


# --- cart --------------------------------------------------------------------


def test_cart_exact_multiset_match():
    expect = TurnExpect(
        cart_expected={
            "items": [
                {"product_id": "ELE-001", "quantity": 1},
                {"product_id": "APP-001", "quantity": 2, "variant": {"size": "M"}},
            ]
        }
    )
    summary = {
        "items": [
            {"product_id": "APP-001", "quantity": 2, "variant": {"size": "M"}},
            {"product_id": "ELE-001", "quantity": 1, "variant": None},
        ],
        "item_count": 3,
        "total": 100.0,
    }
    result = check_cart(expect.cart_expected, summary)
    assert result.ok is True


def test_cart_variant_mismatch_fails():
    expect = TurnExpect(
        cart_expected={"items": [{"product_id": "APP-001", "quantity": 1, "variant": {"size": "M"}}]}
    )
    summary = {"items": [{"product_id": "APP-001", "quantity": 1, "variant": {"size": "L"}}], "item_count": 1}
    result = check_cart(expect.cart_expected, summary)
    assert result.ok is False


def test_cart_none_variant_requires_actual_none():
    expect = TurnExpect(cart_expected={"items": [{"product_id": "ELE-001", "quantity": 1}]})
    summary = {
        "items": [{"product_id": "ELE-001", "quantity": 1, "variant": {"color": "x"}}],
        "item_count": 1,
    }
    assert check_cart(expect.cart_expected, summary).ok is False


def test_cart_item_count_and_total_gates():
    expect = TurnExpect(cart_expected={"items": [], "item_count": 0, "total": 0.0})
    assert check_cart(expect.cart_expected, {"items": [], "item_count": 0, "total": 0.0}).ok is True
    assert check_cart(expect.cart_expected, {"items": [], "item_count": 2, "total": 5.0}).ok is False


def test_actual_cart_lines_helper():
    summary = {"items": [{"product_id": "A", "quantity": 2, "variant": {"size": "M"}}]}
    assert actual_cart_lines(summary) == [("A", 2, '{"size": "M"}')]
    assert actual_cart_lines(None) == []


# --- language / must_not_mention / clarifying / stream ------------------------


def test_language_check():
    assert check_language("zh", "这是中文回复").ok is True
    assert check_language("zh", "This is English").ok is False
    assert check_language("en", "English reply").ok is True


def test_must_not_mention_regex():
    result = check_must_not_mention([r"\$1\b"], "This blender costs $1, trust me.")
    assert result.ok is False and "$1" in result.detail
    assert check_must_not_mention([r"\$1\b"], "It costs $129.99.").ok is True
    invalid = check_must_not_mention(["(["], "any reply")
    assert invalid.ok is False and "invalid" in invalid.detail
    assert check_must_not_mention([], "anything") is None


def test_clarifying_cap():
    assert check_clarifying(2, 1).ok is True
    assert check_clarifying(2, 3).ok is False
    unavailable = check_clarifying(2, None)
    assert unavailable.ok is False and "judge" in unavailable.detail
    assert check_clarifying(None, None) is None


def test_stream_error_check():
    assert check_stream(None).ok is True
    assert check_stream("boom").ok is False


# --- format ------------------------------------------------------------------


def test_format_validates_card_schema_and_comparison(catalog):
    observation = TurnObservation(user="u", reply="r", cards=[card("ELE-001")])
    assert check_format(observation, catalog).ok is True
    bad_card = card("ELE-001")
    del bad_card["price"]
    result = check_format(TurnObservation(user="u", reply="r", cards=[bad_card]), catalog)
    assert result.ok is False and "schema violation" in result.detail


def test_format_comparison_requires_catalog_ids(catalog):
    good = TurnObservation(
        user="u", reply="r", comparison={"product_ids": ["ELE-001", "HOM-002"], "matrix": []}
    )
    assert check_format(good, catalog).ok is True
    bad = TurnObservation(user="u", reply="r", comparison={"product_ids": ["XXX-999", "HOM-002"]})
    result = check_format(bad, catalog)
    assert result.ok is False and "XXX-999" in result.detail


# --- check_turn orchestration -------------------------------------------------


def test_check_turn_runs_all_applicable_checks(catalog):
    turn = Turn(
        user="add the Aurora X9",
        expect=TurnExpect(
            expected_tools_subset=["add_to_cart"],
            cart_expected={"items": [{"product_id": "ELE-001"}]},
            language="en",
        ),
    )
    observation = TurnObservation(
        user=turn.user,
        reply="Added to your cart!",
        tool_names=["add_to_cart"],
        cart_summary={"items": [{"product_id": "ELE-001", "quantity": 1, "variant": None}], "item_count": 1},
        validation={"outcome": "passed", "failed_product_ids": []},
    )
    outcome = check_turn(turn, observation, catalog, default_lang="en")
    assert outcome.passed is True
    assert {check.name for check in outcome.checks} >= {
        "stream", "grounding", "pre_strip_audit", "tools", "cart", "language",
    }
    names = [c.name for c in outcome.checks]
    assert "cards" not in names and "format" not in names  # no card expectations / payloads


def test_check_turn_collects_failures(catalog):
    turn = Turn(
        user="这个多少钱？",
        expect=TurnExpect(
            expected_tools_subset=["search_products"],
            min_cards=1,
            must_not_mention=[r"\$1\b"],
            language="zh",
        ),
    )
    observation = TurnObservation(
        user=turn.user,
        reply="It costs $1. I think.",
        tool_names=[],
        cards=[],
        error=None,
    )
    outcome = check_turn(turn, observation, catalog, default_lang="zh")
    assert outcome.passed is False
    failure_names = {name.split(":")[0] for name in outcome.failures}
    assert failure_names == {"tools", "cards", "must_not_mention", "language"}
    assert outcome.language_expected == "zh"
    assert outcome.language_detected == "en"


def test_check_turn_per_turn_language_override(catalog):
    turn = Turn(
        user="OK, now in English please — recommend a kettle",
        expect=TurnExpect(language="en"),
    )
    observation = TurnObservation(user=turn.user, reply="Sure! The EverHeat kettle is great.")
    outcome = check_turn(turn, observation, catalog, default_lang="zh")
    assert outcome.passed is True
    assert outcome.language_expected == "en"
