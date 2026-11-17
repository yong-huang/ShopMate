"""Golden-set scenario schema + strict loader (Eval Plan §2.2).

One JSON file per scenario under ``backend/evals/golden/``. The loader is
intentionally strict: unknown keys, bad enums, empty turns, or duplicate ids
raise :class:`ScenarioError` at load time — a malformed scenario must fail
the harness before any model call is spent on a run.

Taxonomy (§2.1) — 32 scenarios:

======================  ======  =========================================
Category                Count   Covers
======================  ======  =========================================
guided_discovery        8       fuzzy intent → clarifying → recommendations
product_introduction    5       specs, materials/sizing, care, reviews, honesty
comparison              4       2/3-product, attribute-focused, situational
cart                    5       add variant, add multiple, remove, view, quantity
checkout                3       chat checkout, empty-cart refusal, mixed journey
personalization         3       preference injection, cross-session recall, clear
boundary                4       off-scope, non-existent product, injection, switch
======================  ======  =========================================

Language split: ~half Chinese (``zh``), half English (``en``); the boundary
set owns the one intentional mid-conversation switch (per-turn ``language``
expectation overrides the scenario-level ``lang``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

CATEGORIES: tuple[str, ...] = (
    "guided_discovery",
    "product_introduction",
    "comparison",
    "cart",
    "checkout",
    "personalization",
    "boundary",
)

LANGS: tuple[str, ...] = ("en", "zh")

# §2.1 counts per category; verify_taxonomy enforces the full split.
TAXONOMY_COUNTS: dict[str, int] = {
    "guided_discovery": 8,
    "product_introduction": 5,
    "comparison": 4,
    "cart": 5,
    "checkout": 3,
    "personalization": 3,
    "boundary": 4,
}

# Profile seed keys (the shopper-writable subset of the profile payload,
# System Design 3.6): everything else (field_sources, updated_at,
# interaction_log) is pipeline-owned and never seeded by a scenario.
PROFILE_SEED_KEYS: tuple[str, ...] = (
    "interests",
    "price_band",
    "sizes",
    "likes",
    "dislikes",
    "notes",
)

_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_EXPECT_KEYS: tuple[str, ...] = (
    "expected_tools_subset",
    "expected_tools_excluded",
    "min_cards",
    "max_cards",
    "card_category_any_of",
    "card_price_min",
    "card_price_max",
    "cart_expected",
    "clarifying_questions_max",
    "must_not_mention",
    "language",
)

_TOP_KEYS: tuple[str, ...] = (
    "id",
    "category",
    "lang",
    "title",
    "profile",
    "seed_cart",
    "judge_min",
    "turns",
)

_TURN_KEYS: tuple[str, ...] = ("user", "expect")

_CART_ITEM_KEYS: tuple[str, ...] = ("product_id", "quantity", "variant")


class ScenarioError(ValueError):
    """A scenario file failed schema validation (fail the run, not the turn)."""


def _where(name: str, *path: str) -> str:
    return f"{name}{'.' + '.'.join(path) if path else ''}"


def _check_type(value: object, types: type | tuple, where: str) -> None:
    if not isinstance(value, types):
        raise ScenarioError(f"{where} must be {types}, got {type(value).__name__}")


def _check_keys(obj: dict, allowed: tuple[str, ...], where: str) -> None:
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise ScenarioError(f"{where} has unknown key(s) {unknown}; allowed: {list(allowed)}")


def _str_list(value: object, where: str) -> list[str]:
    _check_type(value, list, where)
    for item in value:
        _check_type(item, str, f"{where}[]")
    return list(value)


def _opt_number(value: object, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioError(f"{where} must be a number, got {type(value).__name__}")
    return float(value)


def _opt_int(value: object, where: str) -> int | None:
    number = _opt_number(value, where)
    if number is not None and number != int(number):
        raise ScenarioError(f"{where} must be an integer, got {value!r}")
    return int(number) if number is not None else None


def _opt_lang(value: object, where: str) -> str | None:
    if value is None:
        return None
    _check_type(value, str, where)
    if value not in LANGS:
        raise ScenarioError(f"{where} must be one of {list(LANGS)}, got {value!r}")
    return value


@dataclass
class CartItemExpect:
    """One expected cart line: product, quantity, and canonical variant."""

    product_id: str
    quantity: int = 1
    variant: dict[str, str] | None = None

    @property
    def key(self) -> tuple[str, int, str]:
        """Hashable identity for multiset comparison (variant = sorted JSON)."""
        variant = json.dumps(self.variant, sort_keys=True) if self.variant else ""
        return (self.product_id, self.quantity, variant)


@dataclass
class CartExpect:
    """``cart_expected`` — asserted against the cart API after the turn."""

    items: list[CartItemExpect] = field(default_factory=list)
    item_count: int | None = None
    total: float | None = None


@dataclass
class TurnExpect:
    """Per-turn expectations (§2.2) — every field optional, any mix allowed."""

    expected_tools_subset: list[str] = field(default_factory=list)
    expected_tools_excluded: list[str] = field(default_factory=list)
    min_cards: int | None = None
    max_cards: int | None = None
    card_category_any_of: list[str] = field(default_factory=list)
    card_price_min: float | None = None
    card_price_max: float | None = None
    cart_expected: CartExpect | None = None
    clarifying_questions_max: int | None = None
    must_not_mention: list[str] = field(default_factory=list)
    language: str | None = None  # per-turn override of the scenario lang

    @property
    def has_tool_expectations(self) -> bool:
        return bool(self.expected_tools_subset or self.expected_tools_excluded)

    @property
    def has_card_expectations(self) -> bool:
        return any(
            value is not None
            for value in (self.min_cards, self.max_cards, self.card_price_min, self.card_price_max)
        ) or bool(self.card_category_any_of)


@dataclass
class Turn:
    """One scripted user turn and what the assistant must do."""

    user: str
    expect: TurnExpect = field(default_factory=TurnExpect)


@dataclass
class Scenario:
    """One golden-set conversation (§2.2 file format)."""

    id: str
    category: str
    lang: str
    title: str
    profile: dict | None  # profile seed (PROFILE_SEED_KEYS subset)
    seed_cart: list[CartItemExpect]
    judge_min: float  # §2.3: per-dimension judge minimum (default 3/5)
    turns: list[Turn]
    source: str = ""  # file path the scenario was loaded from

    @property
    def has_tool_expectations(self) -> bool:
        return any(turn.expect.has_tool_expectations for turn in self.turns)

    @property
    def has_cart_expectations(self) -> bool:
        return any(turn.expect.cart_expected is not None for turn in self.turns)


def _parse_cart_items(items_raw: object, where: str) -> list[CartItemExpect]:
    _check_type(items_raw, list, where)
    items: list[CartItemExpect] = []
    for index, item in enumerate(items_raw):
        item_where = f"{where}[{index}]"
        _check_type(item, dict, item_where)
        _check_keys(item, _CART_ITEM_KEYS, item_where)
        product_id = item.get("product_id")
        _check_type(product_id, str, f"{item_where}.product_id")
        quantity = _opt_int(item.get("quantity", 1), f"{item_where}.quantity") or 1
        if quantity < 1:
            raise ScenarioError(f"{item_where}.quantity must be >= 1")
        variant = item.get("variant")
        if variant is not None:
            _check_type(variant, dict, f"{item_where}.variant")
            variant = {str(k): str(v) for k, v in variant.items()}
        items.append(CartItemExpect(product_id=product_id, quantity=quantity, variant=variant))
    return items


def _parse_cart_expected(value: object, name: str, where: str) -> CartExpect:
    _check_type(value, dict, where)
    _check_keys(value, ("items", "item_count", "total"), where)
    return CartExpect(
        items=_parse_cart_items(value.get("items", []), f"{where}.items"),
        item_count=_opt_int(value.get("item_count"), f"{where}.item_count"),
        total=_opt_number(value.get("total"), f"{where}.total"),
    )


def _parse_expect(value: object, name: str, where: str) -> TurnExpect:
    if value is None:
        return TurnExpect()
    _check_type(value, dict, where)
    _check_keys(value, _EXPECT_KEYS, where)
    cart_expected = None
    if value.get("cart_expected") is not None:
        cart_expected = _parse_cart_expected(value["cart_expected"], name, f"{where}.cart_expected")
    return TurnExpect(
        expected_tools_subset=_str_list(value.get("expected_tools_subset", []), f"{where}.expected_tools_subset"),
        expected_tools_excluded=_str_list(value.get("expected_tools_excluded", []), f"{where}.expected_tools_excluded"),
        min_cards=_opt_int(value.get("min_cards"), f"{where}.min_cards"),
        max_cards=_opt_int(value.get("max_cards"), f"{where}.max_cards"),
        card_category_any_of=_str_list(value.get("card_category_any_of", []), f"{where}.card_category_any_of"),
        card_price_min=_opt_number(value.get("card_price_min"), f"{where}.card_price_min"),
        card_price_max=_opt_number(value.get("card_price_max"), f"{where}.card_price_max"),
        cart_expected=cart_expected,
        clarifying_questions_max=_opt_int(value.get("clarifying_questions_max"), f"{where}.clarifying_questions_max"),
        must_not_mention=_str_list(value.get("must_not_mention", []), f"{where}.must_not_mention"),
        language=_opt_lang(value.get("language"), f"{where}.language"),
    )


def scenario_from_dict(data: object, name: str = "<scenario>", source: str = "") -> Scenario:
    """Validate one scenario dict (§2.2); raise :class:`ScenarioError` on any fault."""
    if not isinstance(data, dict):
        raise ScenarioError(f"{name} must be a JSON object, got {type(data).__name__}")
    _check_keys(data, _TOP_KEYS, name)

    scenario_id = data.get("id")
    _check_type(scenario_id, str, f"{name}.id")
    if not _ID_RE.match(scenario_id):
        raise ScenarioError(
            f"{name}.id must match {_ID_RE.pattern!r} (lowercase kebab-case), got {scenario_id!r}"
        )

    category = data.get("category")
    if category not in CATEGORIES:
        raise ScenarioError(f"{name}.category must be one of {list(CATEGORIES)}, got {category!r}")

    lang = data.get("lang")
    if lang not in LANGS:
        raise ScenarioError(f"{name}.lang must be one of {list(LANGS)}, got {lang!r}")

    title = data.get("title", "")
    _check_type(title, str, f"{name}.title")

    profile = data.get("profile")
    if profile is not None:
        _check_type(profile, dict, f"{name}.profile")
        _check_keys(profile, PROFILE_SEED_KEYS, f"{name}.profile")
        if "interests" in profile:
            _str_list(profile["interests"], f"{name}.profile.interests")
        if "likes" in profile:
            _str_list(profile["likes"], f"{name}.profile.likes")
        if "dislikes" in profile:
            _str_list(profile["dislikes"], f"{name}.profile.dislikes")
        if "notes" in profile:
            _str_list(profile["notes"], f"{name}.profile.notes")
        if "sizes" in profile:
            _check_type(profile["sizes"], dict, f"{name}.profile.sizes")
        if "price_band" in profile:
            band = profile["price_band"]
            if not (
                (isinstance(band, list) and len(band) == 2)
                or isinstance(band, str)
            ):
                raise ScenarioError(
                    f"{name}.profile.price_band must be a [min, max] pair or a label string"
                )

    seed_raw = data.get("seed_cart", [])
    _check_type(seed_raw, list, f"{name}.seed_cart")
    seed_cart = _parse_cart_items(seed_raw, f"{name}.seed_cart")

    judge_min = _opt_number(data.get("judge_min", 3.0), f"{name}.judge_min")
    judge_min = 3.0 if judge_min is None else judge_min
    if not 1.0 <= judge_min <= 5.0:
        raise ScenarioError(f"{name}.judge_min must be within [1, 5], got {judge_min}")

    turns_raw = data.get("turns")
    _check_type(turns_raw, list, f"{name}.turns")
    if not turns_raw:
        raise ScenarioError(f"{name}.turns must contain at least one turn")
    turns: list[Turn] = []
    for index, turn_raw in enumerate(turns_raw):
        turn_where = f"{name}.turns[{index}]"
        _check_type(turn_raw, dict, turn_where)
        _check_keys(turn_raw, _TURN_KEYS, turn_where)
        user = turn_raw.get("user")
        _check_type(user, str, f"{turn_where}.user")
        if not user.strip():
            raise ScenarioError(f"{turn_where}.user must be non-empty")
        expect = _parse_expect(turn_raw.get("expect"), name, f"{turn_where}.expect")
        turns.append(Turn(user=user, expect=expect))

    return Scenario(
        id=scenario_id,
        category=category,
        lang=lang,
        title=title,
        profile=profile,
        seed_cart=seed_cart,
        judge_min=judge_min,
        turns=turns,
        source=source,
    )


def load_scenario_file(path: Path) -> Scenario:
    """Load + validate one scenario JSON file."""
    name = path.name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"{name} is not valid JSON: {exc}") from None
    return scenario_from_dict(data, name=name, source=str(path))


def load_scenarios(directory: Path) -> list[Scenario]:
    """Load every ``*.json`` scenario under ``directory`` (sorted by id).

    Duplicate ids raise immediately — the golden set is a set, and a report
    keyed by scenario id must stay unambiguous.
    """
    if not directory.is_dir():
        raise ScenarioError(f"scenario directory not found: {directory}")
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise ScenarioError(f"no scenario JSON files under {directory}")
    scenarios: list[Scenario] = []
    seen: set[str] = set()
    for path in paths:
        scenario = load_scenario_file(path)
        if scenario.id in seen:
            raise ScenarioError(f"duplicate scenario id {scenario.id!r} ({path.name})")
        seen.add(scenario.id)
        scenarios.append(scenario)
    return scenarios


def verify_taxonomy(scenarios: list[Scenario]) -> list[str]:
    """Check the §2.1 taxonomy counts and the ~half-Chinese language split.

    Returns a list of human-readable violations (empty = golden set intact).
    """
    problems: list[str] = []
    counts: dict[str, int] = {category: 0 for category in CATEGORIES}
    for scenario in scenarios:
        counts[scenario.category] += 1
    for category, expected in TAXONOMY_COUNTS.items():
        if counts[category] != expected:
            problems.append(
                f"category {category}: expected {expected} scenarios, found {counts[category]}"
            )
    zh = sum(1 for scenario in scenarios if scenario.lang == "zh")
    en = len(scenarios) - zh
    half = len(scenarios) / 2
    if not (half - 1 <= zh <= half + 1):
        problems.append(f"language split: expected ~half zh ({half}), found zh={zh} en={en}")
    return problems
