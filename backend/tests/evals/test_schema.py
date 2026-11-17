"""Unit tests for the golden-set schema + strict loader (Eval Plan §2.2)."""

import json

import pytest

from evals.schema import (
    CATEGORIES,
    TAXONOMY_COUNTS,
    ScenarioError,
    load_scenarios,
    scenario_from_dict,
    verify_taxonomy,
)

MINIMAL = {
    "id": "guide-test-en",
    "category": "guided_discovery",
    "lang": "en",
    "turns": [{"user": "hello"}],
}


def test_minimal_scenario_parses_with_defaults():
    scenario = scenario_from_dict(MINIMAL)
    assert scenario.id == "guide-test-en"
    assert scenario.title == ""
    assert scenario.profile is None
    assert scenario.seed_cart == []
    assert scenario.judge_min == 3.0
    assert len(scenario.turns) == 1
    expect = scenario.turns[0].expect
    assert expect.expected_tools_subset == []
    assert expect.cart_expected is None
    assert expect.language is None


def test_full_scenario_round_trips_every_field():
    scenario = scenario_from_dict(
        {
            "id": "cart-qty-zh",
            "category": "cart",
            "lang": "zh",
            "title": "数量修改",
            "profile": {"interests": ["cooking"], "price_band": [30, 100], "sizes": {"shoes": "US 10"}},
            "seed_cart": [{"product_id": "HOM-002", "quantity": 2}],
            "judge_min": 4.0,
            "turns": [
                {
                    "user": "把水壶数量改成三",
                    "expect": {
                        "expected_tools_subset": ["get_cart"],
                        "expected_tools_excluded": ["checkout"],
                        "min_cards": 0,
                        "max_cards": 0,
                        "card_category_any_of": ["Home & Kitchen"],
                        "card_price_min": 1,
                        "card_price_max": 100,
                        "cart_expected": {
                            "items": [{"product_id": "HOM-002", "quantity": 3}],
                            "item_count": 3,
                            "total": 239.97,
                        },
                        "clarifying_questions_max": 0,
                        "must_not_mention": ["\\$\\d+ 万元"],
                        "language": "zh",
                    },
                }
            ],
        }
    )
    expect = scenario.turns[0].expect
    assert expect.expected_tools_subset == ["get_cart"]
    assert expect.expected_tools_excluded == ["checkout"]
    assert expect.min_cards == 0 and expect.max_cards == 0
    assert expect.card_category_any_of == ["Home & Kitchen"]
    assert expect.card_price_min == 1 and expect.card_price_max == 100
    cart = expect.cart_expected
    assert cart is not None
    assert [item.key for item in cart.items] == [("HOM-002", 3, "")]
    assert cart.item_count == 3 and cart.total == pytest.approx(239.97)
    assert expect.clarifying_questions_max == 0
    assert expect.must_not_mention == ["\\$\\d+ 万元"]
    assert expect.language == "zh"
    assert scenario.judge_min == 4.0
    assert scenario.has_cart_expectations and scenario.has_tool_expectations


def test_cart_expected_variant_key_normalizes_sorted_json():
    scenario = scenario_from_dict(
        {
            **MINIMAL,
            "turns": [
                {
                    "user": "hi",
                    "expect": {
                        "cart_expected": {
                            "items": [{"product_id": "APP-001", "variant": {"size": "M", "color": "x"}}]
                        }
                    },
                }
            ],
        }
    )
    item = scenario.turns[0].expect.cart_expected.items[0]
    assert item.key[2] == json.dumps({"size": "M", "color": "x"}, sort_keys=True)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(category="sweepstakes"),
        lambda d: d.update(lang="fr"),
        lambda d: d.update(id="Bad_ID"),
        lambda d: d.update(turns=[]),
        lambda d: d.update(extra_key=True),
        lambda d: d.update(judge_min=9.0),
        lambda d: d.update(turns=[{"user": " ", "expect": {}}]),
    ],
)
def test_top_level_faults_raise(mutate):
    data = json.loads(json.dumps(MINIMAL))
    mutate(data)
    with pytest.raises(ScenarioError):
        scenario_from_dict(data)


def test_unknown_expect_key_raises():
    data = json.loads(json.dumps(MINIMAL))
    data["turns"][0]["expect"] = {"min_cardz": 1}
    with pytest.raises(ScenarioError, match="min_cardz"):
        scenario_from_dict(data)


def test_cart_expected_unknown_key_raises():
    data = json.loads(json.dumps(MINIMAL))
    data["turns"][0]["expect"] = {"cart_expected": {"itmes": []}}
    with pytest.raises(ScenarioError):
        scenario_from_dict(data)


def test_bad_profile_key_raises():
    data = json.loads(json.dumps(MINIMAL))
    data["profile"] = {"field_sources": {"interests": "s1"}}
    with pytest.raises(ScenarioError, match="field_sources"):
        scenario_from_dict(data)


def test_bad_per_turn_language_raises():
    data = json.loads(json.dumps(MINIMAL))
    data["turns"][0]["expect"] = {"language": "de"}
    with pytest.raises(ScenarioError):
        scenario_from_dict(data)


def test_duplicate_ids_raise(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(MINIMAL), encoding="utf-8")
    dup = {**MINIMAL, "id": "guide-dup-en"}
    (tmp_path / "b.json").write_text(json.dumps(dup), encoding="utf-8")
    (tmp_path / "a.json").write_text(
        json.dumps({**MINIMAL, "id": "guide-dup-en"}), encoding="utf-8"
    )
    with pytest.raises(ScenarioError, match="duplicate scenario id"):
        load_scenarios(tmp_path)


def test_malformed_json_raises(tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ScenarioError, match="not valid JSON"):
        load_scenarios(tmp_path)


def test_empty_directory_raises(tmp_path):
    with pytest.raises(ScenarioError, match="no scenario"):
        load_scenarios(tmp_path)


def test_taxonomy_verifier_reports_gaps():
    scenarios = [scenario_from_dict(MINIMAL)]
    problems = verify_taxonomy(scenarios)
    assert any("guided_discovery" in problem for problem in problems)
    assert len(problems) >= len(TAXONOMY_COUNTS) - 1  # one category ok, rest missing


def test_taxonomy_categories_constant_is_complete():
    assert sorted(TAXONOMY_COUNTS) == sorted(CATEGORIES)
    assert sum(TAXONOMY_COUNTS.values()) == 32
