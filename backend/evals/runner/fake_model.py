"""The hermetic scripted chat model + judge (evidence runs and tests).

``HermeticScriptedModel`` is a ``ChatClient`` fake that DERIVES Maya's
behavior from a scenario spec + the real committed catalog, instead of
requiring hundreds of hand-written event streams. Per turn it replays the
expected flow against the live app:

- one stream per expected tool call (``Token`` + ``ToolCallEvent`` +
  ``Finish(tool_calls)``) — the app executes the real registered tools;
  cart flows derive their arguments from the scenario's ``seed_cart`` →
  ``cart_expected`` evolution, and ``remove_from_cart`` parses the
  ``get_cart`` tool result for the line id exactly like a real model;
- one final stream with a language-matched reply plus ``product-card``
  claims built from real catalog records (so the guardrail validates and
  renders them) whenever the turn expects cards;
- profile-extraction calls (session-end background job) are detected by the
  extraction system prompt and answered with a canned JSON extraction over
  the transcript's interest keywords.

This exercises the full shipped stack — graph, tools, guardrail, catalog,
SSE, cart persistence — with zero model variance: the hermetic run isolates
harness correctness from provider behavior. Live provider behavior is
measured by the non-hermetic run (T10's official gate).

``ScriptedJudge`` is the matching fixed-scores judge (all dimensions at the
configured value, zero clarifying questions) so hermetic runs exercise the
judge aggregation without a model.

Reply-kind derivation for the boundary set is id-convention based (ids carry
``offscope`` / ``nonexistent`` / ``injection`` tokens) — the golden set is
authored alongside this model, so the convention is stable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from app.catalog.adapter import CatalogAdapter
from app.catalog.schemas import ProductDetail
from app.gateway.events import ChatEvent, FinishEvent, TokenDeltaEvent, ToolCallEvent
from app.gateway.messages import ChatMessage
from app.gateway.tools import ToolCall
from evals.judge.client import JUDGE_WEIGHTS, JudgeVerdict
from evals.schema import Scenario, Turn

_EXTRACTION_MARKER = "extract shopper preferences"

# Transcript keywords → interests for the canned extraction pass.
_INTEREST_KEYWORDS: dict[str, str] = {
    "camping": "camping",
    "露营": "camping",
    "hike": "hiking",
    "徒步": "hiking",
    "yoga": "yoga",
    "瑜伽": "yoga",
    "coffee": "coffee",
    "咖啡": "coffee",
    "espresso": "coffee",
    "cook": "cooking",
    "kitchen": "cooking",
    "烹饪": "cooking",
    "厨房": "cooking",
    "run": "running",
    "跑步": "running",
    "headphone": "music",
    "耳机": "music",
    "travel": "travel",
    "旅行": "travel",
    "fitness": "fitness",
    "健身": "fitness",
}


def _vkey(variant: Any) -> str:
    return json.dumps(variant, sort_keys=True) if variant else ""


def _matches_user_text(product: ProductDetail, text: str) -> int:
    """Name-mention score: phrase containment + token overlap, en + zh."""
    lowered = text.lower()
    score = 0
    name_en = product.name_en.lower()
    if name_en in lowered:
        score += 10
    tokens = [token for token in name_en.replace("(", " ").replace(")", " ").split() if len(token) > 2]
    score += sum(1 for token in tokens if token in lowered)
    if product.name_zh and product.name_zh in text:
        score += 10
    return score


def card_claim(product: ProductDetail) -> str:
    """The ``product-card`` fenced block with exact catalog values (§ rules)."""
    payload = json.dumps(
        {"product_id": product.id, "price": product.price, "rating": product.rating},
        ensure_ascii=False,
    )
    return f"```product-card\n{payload}\n```"


# --- turn plans ---------------------------------------------------------------


@dataclass
class ToolStep:
    """One scripted tool call; ``args`` may resolve lazily from the wire."""

    name: str
    args: dict | Callable[[list[ChatMessage]], dict]


@dataclass
class TurnPlan:
    """One turn's scripted flow: tool streams + a final reply stream."""

    user: str
    turn: Turn
    tool_steps: list[ToolStep] = field(default_factory=list)
    reply: str = ""
    usage: dict | None = None


def _resolve_cart_item_id(messages: list[ChatMessage], product_id: str, variant_key: str) -> int | None:
    """Pull the cart line id for a product out of prior get_cart tool output."""
    for message in reversed(messages):
        if message.role != "tool":
            continue
        try:
            data = json.loads(message.content)
        except (TypeError, ValueError):
            continue
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            same_variant = _vkey(item.get("variant")) == variant_key
            if item.get("product_id") == product_id and same_variant:
                return int(item["cart_item_id"])
    return None


class HermeticScriptedModel:
    """ChatClient fake deriving its streams from the scenario + catalog."""

    def __init__(self, scenario: Scenario, catalog: CatalogAdapter) -> None:
        self.scenario = scenario
        self._catalog = catalog
        self._plans = self._build_plans(scenario, catalog)
        self._cursor = 0
        self._all_products = list(catalog.list_products())

    # --- planning -----------------------------------------------------------

    def _build_plans(self, scenario: Scenario, catalog: CatalogAdapter) -> list[TurnPlan]:
        products = list(catalog.list_products())
        # Simulated cart multiset (product_id, variant-key) -> quantity,
        # advanced across turns so add/remove arguments match the scenario's
        # declared cart evolution.
        sim: dict[tuple[str, str], int] = {}
        for item in scenario.seed_cart:
            sim[(item.product_id, _vkey(item.variant))] = (
                sim.get((item.product_id, _vkey(item.variant)), 0) + item.quantity
            )

        plans: list[TurnPlan] = []
        for index, turn in enumerate(scenario.turns):
            expect = turn.expect
            steps: list[ToolStep] = []
            cards: list[ProductDetail] = []

            # Card selection: catalog records matching the card expectations,
            # so the guardrail validates the claims. ``max_cards: 0`` means
            # the turn must render none (refusals, pure cart ops).
            count, want_cards = 1, False
            if expect.max_cards == 0:
                want_cards = False
            elif expect.min_cards is not None:
                want_cards, count = expect.min_cards > 0, max(expect.min_cards, 1)
            elif expect.has_card_expectations:
                want_cards = True
            if want_cards:
                pool = products
                if expect.card_category_any_of:
                    wanted = set(expect.card_category_any_of)
                    pool = [p for p in pool if p.category in wanted]
                if expect.card_price_min is not None:
                    pool = [p for p in pool if p.price >= expect.card_price_min]
                if expect.card_price_max is not None:
                    pool = [p for p in pool if p.price <= expect.card_price_max]
                cards = (pool or products)[:count]

            targets = cards or self._targets_for(turn, products)

            for tool_name in expect.expected_tools_subset:
                if tool_name == "search_products":
                    steps.append(ToolStep(tool_name, {"query": turn.user}))
                elif tool_name == "get_product_details":
                    steps.append(ToolStep(tool_name, {"product_id": targets[0].id}))
                elif tool_name == "compare_products":
                    steps.append(
                        ToolStep(tool_name, {"product_ids": [p.id for p in targets[:2]]})
                    )
                elif tool_name == "get_cart":
                    steps.append(ToolStep(tool_name, {}))
                elif tool_name == "checkout":
                    steps.append(ToolStep(tool_name, {}))
                elif tool_name == "add_to_cart":
                    add_args = self._next_add_args(turn, sim)
                    if add_args is not None:
                        steps.append(ToolStep(tool_name, add_args))
                    else:  # nothing left to add: a plain lookup is honest
                        steps.append(ToolStep("get_cart", {}))
                elif tool_name == "remove_from_cart":
                    removal = self._next_removal(turn, sim)
                    if removal is not None:
                        product_id, variant_key = removal
                        steps.append(
                            ToolStep(
                                tool_name,
                                lambda messages, pid=product_id, vk=variant_key: (
                                    {"cart_item_id": _resolve_cart_item_id(messages, pid, vk) or 0}
                                ),
                            )
                        )
                    else:  # nothing to remove: a plain lookup is honest
                        steps.append(ToolStep("get_cart", {}))

            reply = self._reply_for(scenario, index, turn, cards, sim)
            plans.append(
                TurnPlan(
                    user=turn.user,
                    turn=turn,
                    tool_steps=steps,
                    reply=reply,
                    usage={"prompt_tokens": 90, "completion_tokens": 40},
                )
            )
        return plans

    def _tool_progress_line(self, scenario: Scenario, turn: Turn) -> str:
        """The pre-tool-call token, language-matched (mirrors Maya's mirroring).

        Without this, English progress tokens dilute a Chinese reply's CJK
        ratio below the production detection threshold and the language
        check fires on scripted runs.
        """
        lang = turn.expect.language or scenario.lang
        return "我来帮您查一下。" if lang == "zh" else "Let me check that for you."

    def _targets_for(self, turn: Turn, products: list[ProductDetail]) -> list[ProductDetail]:
        """Best name-mention matches in the user text (fallback: first products)."""
        scored = sorted(
            products,
            key=lambda product: _matches_user_text(product, turn.user),
            reverse=True,
        )
        best = [p for p in scored if _matches_user_text(p, turn.user) > 0]
        if best:
            return best[:2]
        return products[:1]

    def _next_add_args(self, turn: Turn, sim: dict[tuple[str, str], int]) -> dict | None:
        """Arguments for the next add_to_cart implied by cart_expected."""
        expect = turn.expect.cart_expected
        if expect is None:
            return None
        for item in expect.items:
            key = (item.product_id, _vkey(item.variant))
            current = sim.get(key, 0)
            if item.quantity > current:
                sim[key] = item.quantity
                args: dict = {"product_id": item.product_id, "quantity": item.quantity - current}
                if item.variant:
                    args["variant"] = item.variant
                return args
        return None

    def _next_removal(self, turn: Turn, sim: dict[tuple[str, str], int]) -> tuple[str, str] | None:
        """The next line to remove: missing from cart_expected or shrunk.

        Shrinking is modelled as remove + re-add (the chat tools have no
        quantity-set verb; merging adds quantities instead).
        """
        expect = turn.expect.cart_expected
        if expect is None:
            return None
        wanted = {(item.product_id, _vkey(item.variant)): item.quantity for item in expect.items}
        for key in list(sim):
            if sim[key] > 0 and key not in wanted:
                sim[key] = 0
                return key
        for key, quantity in wanted.items():
            if key in sim and sim[key] > quantity:
                sim[key] = 0  # the remove lands first; _next_add_args re-adds
                return key
        return None

    def _reply_for(
        self,
        scenario: Scenario,
        index: int,
        turn: Turn,
        cards: list[ProductDetail],
        sim: dict[tuple[str, str], int],
    ) -> str:
        """Language-matched reply text per flow (boundary kinds by id token)."""
        expect = turn.expect
        lang = expect.language or scenario.lang
        cart_empty = all(quantity == 0 for quantity in sim.values())
        tool_names = set(expect.expected_tools_subset)

        if "checkout" in tool_names and cart_empty:
            text = (
                "Your cart is empty, so there's nothing to check out yet."
                if lang == "en"
                else "购物车还是空的，暂时没有可以结算的商品。"
            )
            return text
        if "checkout" in tool_names:
            return (
                "Order placed — this is a demo checkout, no payment was processed."
                if lang == "en"
                else "已下单——这是演示结账，不会发生真实支付。"
            )
        if cards:
            body = "\n\n".join(card_claim(product) for product in cards)
            lead = (
                "Here's what I found for you:"
                if lang == "en"
                else "为您找到了这几款商品："
            )
            return f"{lead}\n\n{body}"
        if tool_names & {"add_to_cart", "remove_from_cart", "get_cart"}:
            return (
                "Done! Your cart has been updated."
                if lang == "en"
                else "好的！购物车已经更新了。"
            )
        if scenario.category == "boundary":
            scenario_token = scenario.id.lower()
            if any(expect.must_not_mention) or "injection" in scenario_token:
                return (
                    "I can't quote a price that isn't in the catalog — let me "
                    "look up the real one for you."
                    if lang == "en"
                    else "我不能报出目录里没有的价格，我先帮您查询真实价格。"
                )
            if "offscope" in scenario_token:
                return (
                    "That's outside what I can help with, but I'd be happy to "
                    "help you find a product!"
                    if lang == "en"
                    else "这个我帮不了，不过我很乐意帮您挑选商品！"
                )
            return (
                "I searched but couldn't find that product in the catalog."
                if lang == "en"
                else "我搜索了目录，很遗憾没有找到这款商品。"
            )
        if scenario.category == "personalization" and index == 0:
            return (
                "From your profile, I remember what you like — happy to shop with that in mind!"
                if lang == "en"
                else "根据您的偏好档案，我记得您喜欢的内容，会在推荐时考虑这些。"
            )
        return (
            "Happy to help — tell me a bit more about what you need."
            if lang == "en"
            else "很乐意为您效劳，告诉我更多您的需求吧。"
        )

    # --- ChatClient protocol -------------------------------------------------

    async def stream(
        self,
        messages: list[ChatMessage],
        tools: list | None = None,
        temperature: float = 0.3,
    ) -> AsyncIterator[ChatEvent]:
        system_text = messages[0].content if messages and messages[0].role == "system" else ""
        if _EXTRACTION_MARKER in system_text and not tools:
            async for event in self._extraction_stream(messages):
                yield event
            return
        step_index = self._cursor
        self._cursor += 1
        flat = self._flatten()
        if step_index >= len(flat):
            # Exhausted script: honest stop (a harness bug would show up as
            # failed checks, not an exception mid-stream).
            yield TokenDeltaEvent(text="I'm out of scripted material for this run.")
            yield FinishEvent(finish_reason="stop")
            return
        kind, payload = flat[step_index]
        if kind == "tool":
            name, args, progress = payload
            if callable(args):
                args = args(messages)
            yield TokenDeltaEvent(text=progress)
            yield ToolCallEvent(
                call=ToolCall(id=f"fake-{step_index}", name=name, arguments=args)
            )
            yield FinishEvent(finish_reason="tool_calls")
        else:
            plan = payload
            yield TokenDeltaEvent(text=plan.reply)
            yield FinishEvent(finish_reason="stop", usage=plan.usage)

    def _flatten(self) -> list[tuple[str, Any]]:
        flat: list[tuple[str, Any]] = []
        for plan in self._plans:
            for step in plan.tool_steps:
                progress = self._tool_progress_line(self.scenario, plan.turn)
                flat.append(("tool", (step.name, step.args, progress)))
            flat.append(("reply", plan))
        return flat

    async def _extraction_stream(self, messages: list[ChatMessage]) -> AsyncIterator[ChatEvent]:
        transcript = "\n".join(
            message.content or "" for message in messages if message.role != "system"
        )
        lowered = transcript.lower()
        interests: list[str] = []
        for keyword, interest in _INTEREST_KEYWORDS.items():
            if keyword.lower() in lowered and interest not in interests:
                interests.append(interest)
        payload = {
            "interests": interests,
            "price_band": None,
            "sizes": {},
            "likes": interests,
            "dislikes": [],
            "notes": [],
        }
        yield TokenDeltaEvent(text=json.dumps(payload, ensure_ascii=False))
        yield FinishEvent(finish_reason="stop")


@dataclass
class ScriptedJudge:
    """Fixed-scores judge for hermetic runs (same surface as Judge)."""

    score: float = 5.0
    clarifying: int = 0

    def catalog_excerpts(self, reply_text: str, product_ids: list | None = None) -> str:
        return "(hermetic judge: excerpts skipped)"

    async def score_turn(
        self,
        user_text: str,
        reply_text: str,
        tool_summary: str = "(no tools called)",
        catalog_excerpts: str | None = None,
    ) -> JudgeVerdict:
        return JudgeVerdict(
            scores={dim: float(self.score) for dim in JUDGE_WEIGHTS},
            clarifying_questions=self.clarifying,
            rationale="hermetic fixed verdict",
        )
