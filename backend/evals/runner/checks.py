"""Rule-check engine (Eval Plan §3.1) — deterministic, binary, per-turn.

Pure functions: :func:`check_turn` compares one turn's :class:`TurnObservation`
(what the runner saw on the SSE wire / cart API / turn log) against the
scenario's ``expect`` block and the catalog. No I/O, no model calls — the only
judge-dependent check (``clarifying_questions_max``) takes the verdict as an
argument.

Check inventory (§3.1 + §2.2):

- ``grounding``        — every rendered ``product_card`` id exists in the
  catalog; **any rendered fabricated id is a hard failure** regardless of rate.
- ``pre_strip_audit``  — the turn-log validation record (``chat_turn`` →
  ``validation.failed_product_ids``) exposes card ids the guardrail absorbed;
  ids that are not in the catalog are grounding failures the shopper never saw.
- ``tools``            — executed tool names contain the expected subset
  (order-insensitive) and none of the excluded ones.
- ``cards``            — min/max card count, category membership, price band
  over the rendered card payloads.
- ``cart``             — ``cart_expected`` vs. the cart API summary: exact
  (product_id, quantity, variant) multiset match plus optional item_count /
  total.
- ``language``         — CJK-ratio heuristic (≥ 0.2 → zh, else en), matching
  the endpoint's ``_detect_language`` so eval and production agree.
- ``must_not_mention`` — regex denial list over the reply text.
- ``clarifying``       — judge-counted clarifying questions within the cap.
- ``format``           — rendered card payloads validate against the
  ``ProductCardPayload`` schema; comparison payloads reference 2-3 catalog
  product ids (feeds the ``format_compliance`` metric).
- ``stream``           — the turn produced no SSE ``error`` event.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.agent.cards import ProductCardPayload, _CARD_BLOCK
from app.catalog.adapter import CatalogAdapter
from evals.schema import CartExpect, Turn, TurnExpect

CJK_RATIO_ZH_THRESHOLD = 0.2  # mirrors app.api.chat._detect_language


def strip_card_blocks(text: str) -> str:
    """The reply as the shopper sees it: ``product-card`` fences removed.

    The frontend strips card blocks before display, so language detection
    must not let ASCII card JSON dilute the CJK ratio of a Chinese reply.
    """
    return _CARD_BLOCK.sub("", text)


def detect_language(text: str) -> str | None:
    """The production CJK-ratio heuristic: zh at ≥ 0.2 CJK share, else en."""
    stripped = strip_card_blocks(text).strip()
    if not stripped:
        return None
    cjk = sum(1 for char in stripped if "\u4e00" <= char <= "\u9fff")
    return "zh" if cjk / len(stripped) >= CJK_RATIO_ZH_THRESHOLD else "en"


@dataclass
class CheckResult:
    """One binary rule check with a short explanation."""

    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class GroundingAudit:
    """Per-turn grounding facts (§3.1 ``product_id_grounding_rate`` inputs)."""

    rendered_card_ids: list[str] = field(default_factory=list)
    fabricated_rendered: list[str] = field(default_factory=list)
    pre_strip_ids: list[str] = field(default_factory=list)
    fabricated_pre_strip: list[str] = field(default_factory=list)

    def merge(self, other: "GroundingAudit") -> "GroundingAudit":
        return GroundingAudit(
            rendered_card_ids=self.rendered_card_ids + other.rendered_card_ids,
            fabricated_rendered=self.fabricated_rendered + other.fabricated_rendered,
            pre_strip_ids=self.pre_strip_ids + other.pre_strip_ids,
            fabricated_pre_strip=self.fabricated_pre_strip + other.fabricated_pre_strip,
        )

    @property
    def grounding_rate(self) -> float | None:
        """Rendered-valid + pre-strip-catalog ids ÷ (rendered + pre-strip ids).

        ``None`` when the scenario rendered no cards and recorded no pre-strip
        failures — nothing was measured (excluded from the run rate).
        """
        total = len(self.rendered_card_ids) + len(self.pre_strip_ids)
        if total == 0:
            return None
        valid = len(self.rendered_card_ids) - len(self.fabricated_rendered) + len(
            self.pre_strip_ids
        ) - len(self.fabricated_pre_strip)
        return valid / total

    @property
    def hard_fail(self) -> bool:
        """Any rendered fabricated id = hard fail (§3.1), rate notwithstanding."""
        return bool(self.fabricated_rendered)


@dataclass
class TurnObservation:
    """What the runner observed for one turn (SSE + cart API + turn log)."""

    user: str
    reply: str
    cards: list[dict[str, Any]] = field(default_factory=list)
    comparison: dict[str, Any] | None = None
    cart_update: dict[str, Any] | None = None
    tool_names: list[str] = field(default_factory=list)  # executed, in order
    validation: dict[str, Any] | None = None  # chat_turn validation record
    cart_summary: dict[str, Any] | None = None  # cart API after the turn
    judge_scores: dict[str, float] | None = None  # set after the judge pass
    judge_clarifying: int | None = None
    error: str | None = None  # SSE error event message, if any

    @property
    def structured_payloads(self) -> list[Any]:
        """Card payloads + the comparison payload (format-compliance scope)."""
        payloads: list[Any] = list(self.cards)
        if self.comparison is not None:
            payloads.append(self.comparison)
        return payloads


def _variant_key(variant: Any) -> str:
    if not variant:
        return ""
    return json.dumps(variant, sort_keys=True)


def actual_cart_lines(summary: dict[str, Any] | None) -> list[tuple[str, int, str]]:
    """The cart summary's lines as (product_id, quantity, variant-key) tuples."""
    if not summary:
        return []
    return [
        (str(item["product_id"]), int(item["quantity"]), _variant_key(item.get("variant")))
        for item in summary.get("items", [])
    ]


def expected_cart_lines(expect_items: list) -> list[tuple[str, int, str]]:
    """The expected multiset identity (variant None must match None exactly)."""
    return [
        (item.product_id, item.quantity, _variant_key(item.variant))
        for item in expect_items
    ]


# --- individual checks -------------------------------------------------------


def check_grounding(cards: list[dict], catalog: CatalogAdapter) -> tuple[CheckResult, GroundingAudit]:
    """Rendered card ids must exist in the catalog (hard fail otherwise)."""
    audit = GroundingAudit(rendered_card_ids=[str(card.get("product_id")) for card in cards])
    for card in cards:
        product_id = str(card.get("product_id", ""))
        if catalog.get_product(product_id) is None:
            audit.fabricated_rendered.append(product_id)
    ok = not audit.fabricated_rendered
    detail = "" if ok else f"rendered fabricated card id(s): {audit.fabricated_rendered}"
    return CheckResult("grounding", ok, detail), audit


def check_pre_strip(validation: dict | None, catalog: CatalogAdapter) -> tuple[CheckResult, GroundingAudit]:
    """Audit the guardrail's pre-strip record for absorbed grounding failures.

    The guardrail did its job (the shopper never saw these), but the eval
    counts model-side fabrication: a stripped id that is not in the catalog
    means the model invented a product. Stripped ids that DO exist failed on
    price/rating drift — inaccuracy, not a fabricated product id.
    """
    failed_ids = list((validation or {}).get("failed_product_ids") or [])
    audit = GroundingAudit(pre_strip_ids=[str(pid) for pid in failed_ids])
    for product_id in audit.pre_strip_ids:
        if catalog.get_product(product_id) is None:
            audit.fabricated_pre_strip.append(product_id)
    ok = not audit.fabricated_pre_strip
    detail = "" if ok else f"guardrail stripped non-catalog card id(s): {audit.fabricated_pre_strip}"
    return CheckResult("pre_strip_audit", ok, detail), audit


def check_tools(expect: TurnExpect, tool_names: list[str]) -> CheckResult | None:
    """Expected subset present (order-insensitive); excluded absent."""
    if not expect.has_tool_expectations:
        return None
    actual = set(tool_names)
    missing = sorted(set(expect.expected_tools_subset) - actual)
    excluded_called = sorted(set(expect.expected_tools_excluded) & actual)
    ok = not missing and not excluded_called
    details = []
    if missing:
        details.append(f"missing expected tool(s) {missing}")
    if excluded_called:
        details.append(f"called excluded tool(s) {excluded_called}")
    return CheckResult("tools", ok, "; ".join(details))


def check_cards(expect: TurnExpect, cards: list[dict], catalog: CatalogAdapter) -> CheckResult | None:
    """min/max count + category membership + price band over rendered cards."""
    if not expect.has_card_expectations:
        return None
    details: list[str] = []
    if expect.min_cards is not None and len(cards) < expect.min_cards:
        details.append(f"rendered {len(cards)} cards, min {expect.min_cards}")
    if expect.max_cards is not None and len(cards) > expect.max_cards:
        details.append(f"rendered {len(cards)} cards, max {expect.max_cards}")
    if expect.card_category_any_of:
        categories = set(expect.card_category_any_of)
        matched = [
            card for card in cards
            if (product := catalog.get_product(str(card.get("product_id")))) is not None
            and product.category in categories
        ]
        if not matched:
            details.append(f"no card in categories {sorted(categories)}")
    if expect.card_price_min is not None or expect.card_price_max is not None:
        for card in cards:
            price = card.get("price")
            if isinstance(price, bool) or not isinstance(price, (int, float)):
                details.append(f"card {card.get('product_id')} has non-numeric price {price!r}")
                continue
            if expect.card_price_min is not None and price < expect.card_price_min:
                details.append(f"card {card.get('product_id')} price {price} < min {expect.card_price_min}")
            if expect.card_price_max is not None and price > expect.card_price_max:
                details.append(f"card {card.get('product_id')} price {price} > max {expect.card_price_max}")
    return CheckResult("cards", not details, "; ".join(details))


def check_cart(expect: CartExpect, summary: dict | None) -> CheckResult:
    """``cart_expected`` vs. the cart API summary (exact multiset match)."""
    actual = sorted(actual_cart_lines(summary))
    wanted = sorted(expected_cart_lines(expect.items))
    details: list[str] = []
    if actual != wanted:
        details.append(f"cart lines {actual} != expected {wanted}")
    if expect.item_count is not None and (summary or {}).get("item_count") != expect.item_count:
        details.append(
            f"item_count {(summary or {}).get('item_count')} != expected {expect.item_count}"
        )
    if expect.total is not None:
        total = (summary or {}).get("total")
        if not isinstance(total, (int, float)) or abs(float(total) - expect.total) > 0.01:
            details.append(f"total {total} != expected {expect.total}")
    return CheckResult("cart", not details, "; ".join(details))


def check_language(expected: str, reply: str) -> CheckResult:
    detected = detect_language(reply)
    ok = detected == expected
    detail = "" if ok else f"reply detected {detected}, expected {expected}"
    return CheckResult("language", ok, detail)


def check_must_not_mention(patterns: list[str], reply: str) -> CheckResult | None:
    if not patterns:
        return None
    details: list[str] = []
    for pattern in patterns:
        try:
            if re.search(pattern, reply):
                details.append(f"reply mentions forbidden pattern {pattern!r}")
        except re.error:
            details.append(f"invalid must_not_mention regex {pattern!r}")
    return CheckResult("must_not_mention", not details, "; ".join(details))


def check_clarifying(max_questions: int | None, clarifying: int | None) -> CheckResult | None:
    if max_questions is None:
        return None
    if clarifying is None:
        return CheckResult("clarifying", False, "judge verdict unavailable for clarifying count")
    ok = clarifying <= max_questions
    detail = "" if ok else f"judge counted {clarifying} clarifying questions, max {max_questions}"
    return CheckResult("clarifying", ok, detail)


def check_format(observation: TurnObservation, catalog: CatalogAdapter) -> CheckResult | None:
    """Card payloads must match the SSE schema; comparisons reference the catalog."""
    payloads = observation.structured_payloads
    if not payloads:
        return None
    details: list[str] = []
    for card in observation.cards:
        try:
            ProductCardPayload.model_validate(card)
        except Exception as exc:  # noqa: BLE001 — pydantic error text is the detail
            details.append(f"card payload schema violation: {exc}")
    comparison = observation.comparison
    if comparison is not None:
        ids = comparison.get("product_ids")
        if not isinstance(ids, list) or not (2 <= len(ids) <= 3):
            details.append(f"comparison product_ids must be a 2-3 id list, got {ids!r}")
        else:
            unknown = [str(pid) for pid in ids if catalog.get_product(str(pid)) is None]
            if unknown:
                details.append(f"comparison references non-catalog id(s) {unknown}")
    return CheckResult("format", not details, "; ".join(details))


def check_stream(error: str | None) -> CheckResult:
    return CheckResult("stream", error is None, error or "")


# --- orchestration -----------------------------------------------------------


@dataclass
class TurnCheckOutcome:
    """All rule checks for one turn, plus the grounding facts they found."""

    checks: list[CheckResult] = field(default_factory=list)
    grounding: GroundingAudit = field(default_factory=GroundingAudit)
    language_expected: str | None = None
    language_detected: str | None = None

    @property
    def passed(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> list[str]:
        return [
            f"{check.name}: {check.detail}" if check.detail else f"{check.name}"
            for check in self.checks
            if not check.ok
        ]

    def to_dicts(self) -> list[dict[str, Any]]:
        return [check.to_dict() for check in self.checks]


def check_turn(
    turn: Turn,
    observation: TurnObservation,
    catalog: CatalogAdapter,
    default_lang: str,
) -> TurnCheckOutcome:
    """Run every rule check relevant to one turn (§3.1 / §2.2)."""
    expect = turn.expect
    checks: list[CheckResult] = []
    grounding = GroundingAudit()

    checks.append(check_stream(observation.error))
    result, audit = check_grounding(observation.cards, catalog)
    checks.append(result)
    grounding = grounding.merge(audit)
    result, audit = check_pre_strip(observation.validation, catalog)
    checks.append(result)
    grounding = grounding.merge(audit)

    tools_result = check_tools(expect, observation.tool_names)
    if tools_result is not None:
        checks.append(tools_result)
    cards_result = check_cards(expect, observation.cards, catalog)
    if cards_result is not None:
        checks.append(cards_result)
    if expect.cart_expected is not None:
        checks.append(check_cart(expect.cart_expected, observation.cart_summary))
    checks.append(check_must_not_mention(expect.must_not_mention, observation.reply) or CheckResult("must_not_mention", True))
    clarifying = check_clarifying(expect.clarifying_questions_max, observation.judge_clarifying)
    if clarifying is not None:
        checks.append(clarifying)
    format_result = check_format(observation, catalog)
    if format_result is not None:
        checks.append(format_result)

    language_expected = expect.language or default_lang
    language_result = check_language(language_expected, observation.reply)
    checks.append(language_result)

    return TurnCheckOutcome(
        checks=checks,
        grounding=grounding,
        language_expected=language_expected,
        language_detected=detect_language(observation.reply),
    )
