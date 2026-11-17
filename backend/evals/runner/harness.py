"""The scenario harness: live-app fixture, seeding, and ``run_scenario``.

``eval_app`` builds the REAL app (composition root, System Design 3.7) on a
throwaway SQLite database — the same shape the backend tests use
(``create_app(settings, chat_client=...)``). ``chat_client=None`` means the
configured provider facade (a live provider run); a scripted model means a
hermetic evidence run.

``run_scenario`` executes one golden scenario against an opened app:

1. seed the profile row and the cart (REST) when the scenario declares them;
2. per turn: SSE chat turn → turn-log record → judge pass → rule checks;
3. run the scenario's between-turn actions (``end_session`` drains the
   scheduled profile extraction; ``clear_profile`` hits DELETE /api/profile);
4. assert ``cart_expected`` through the cart API after the declaring turn;
5. return a :class:`ScenarioResult` with the full evidence chain (§4 report
   contents): transcript, tool log, card events incl. the pre-strip audit,
   cart assertions, and judge dimension scores.
"""

from __future__ import annotations

import shutil
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI

from app.catalog.adapter import CatalogAdapter
from app.config import Settings
from app.db import ensure_profile
from app.gateway.client import ChatClient
from app.main import create_app
from app.profile.service import pending_extractions
from evals.judge.client import JUDGE_WEIGHTS, Judge, JudgeError
from evals.runner.capture import TurnLogCapture
from evals.runner.checks import GroundingAudit, TurnCheckOutcome, TurnObservation, check_turn
from evals.runner.session import ChatSession
from evals.schema import Scenario, ScenarioAction, Turn


async def eval_app_generator(
    settings: Settings | None = None,
    chat_client: ChatClient | None = None,
) -> AsyncIterator[FastAPI]:
    """Raw async-generator twin of :func:`eval_app` (manual __anext__ driving).

    The backend tests drive app lifespans by hand (``tests.test_chat_api
    .open_app``); tests and callers that cannot use ``async with`` take this
    form and advance it manually.
    """
    tmp = Path(tempfile.mkdtemp(prefix="shopmate-eval-"))
    if settings is None:
        settings = Settings(
            _env_file=None,
            database_url=f"sqlite+aiosqlite:///{tmp}/shopmate.db",
            checkpoint_db_url=f"sqlite+aiosqlite:///{tmp}/checkpoints.db",
            embedding_provider="none",
        )
    app = create_app(settings=settings, chat_client=chat_client)
    try:
        async with app.router.lifespan_context(app):
            yield app
    finally:
        await app.state.engine.dispose()
        shutil.rmtree(tmp, ignore_errors=True)


@asynccontextmanager
async def eval_app(
    settings: Settings | None = None,
    chat_client: ChatClient | None = None,
) -> AsyncIterator[FastAPI]:
    """A live ShopMate app on a temp SQLite DB (both DBs + index hermetic).

    ``settings=None`` builds a hermetic default (env file ignored,
    keyword-only retrieval). Live provider runs pass ambient settings with
    the database URLs overridden onto the temp dir.
    """
    async with eval_app_generator(settings=settings, chat_client=chat_client) as app:
        yield app


async def seed_profile(app: FastAPI, user_id: str, profile_data: dict) -> None:
    """Write the scenario's profile seed onto the shopper's Profile row.

    ``price_band`` accepts the §2.2 ``[min, max]`` pair (normalized to the
    ``$min-max`` label the pipeline stores) or an explicit label string.
    """
    if not profile_data:
        return
    async with app.state.sessionmaker() as db:
        profile = await ensure_profile(db, user_id)
        if "interests" in profile_data:
            profile.interests = [str(item) for item in profile_data["interests"]]
        if "price_band" in profile_data:
            band = profile_data["price_band"]
            if isinstance(band, list) and len(band) == 2:
                profile.price_band = f"${band[0]}-{band[1]}"
            else:
                profile.price_band = str(band)
        if "sizes" in profile_data:
            profile.sizes = {str(k): str(v) for k, v in profile_data["sizes"].items()}
        if "likes" in profile_data:
            profile.likes = [str(item) for item in profile_data["likes"]]
        if "dislikes" in profile_data:
            profile.dislikes = [str(item) for item in profile_data["dislikes"]]
        if "notes" in profile_data:
            profile.notes = [str(item) for item in profile_data["notes"]]
        await db.commit()


# --- result model ------------------------------------------------------------


@dataclass
class TurnResult:
    """One turn's full evidence (§4: transcript, tool log, audit, judge)."""

    index: int
    user: str
    reply: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # SSE progress
    tool_hops: list[dict[str, Any]] = field(default_factory=list)  # executed
    cards: list[dict[str, Any]] = field(default_factory=list)
    comparison: dict[str, Any] | None = None
    cart_update: dict[str, Any] | None = None
    cart_assert: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None  # pre-strip audit record
    language: dict[str, Any] | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    judge: dict[str, Any] | None = None
    judge_error: str | None = None
    error: str | None = None
    passed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "user": self.user,
            "reply": self.reply,
            "tool_calls": self.tool_calls,
            "tool_hops": self.tool_hops,
            "cards": self.cards,
            "comparison": self.comparison,
            "cart_update": self.cart_update,
            "cart_assert": self.cart_assert,
            "pre_strip_audit": self.validation,
            "language": self.language,
            "checks": self.checks,
            "judge": self.judge,
            "judge_error": self.judge_error,
            "error": self.error,
            "passed": self.passed,
        }


@dataclass
class ScenarioResult:
    """One scenario's aggregated outcome + evidence."""

    scenario: Scenario
    turns: list[TurnResult] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    grounding: GroundingAudit = field(default_factory=GroundingAudit)
    judge_failed: bool = False
    session_id: str = ""
    user_id: str = ""

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def judge_overall(self) -> float | None:
        weighted = [
            turn.judge["weighted"] for turn in self.turns if turn.judge is not None
        ]
        return sum(weighted) / len(weighted) if weighted else None

    def dimension_means(self) -> dict[str, float]:
        scored = [turn.judge["scores"] for turn in self.turns if turn.judge is not None]
        if not scored:
            return {}
        return {
            dim: sum(scores[dim] for scores in scored) / len(scored)
            for dim in JUDGE_WEIGHTS
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.scenario.id,
            "category": self.scenario.category,
            "lang": self.scenario.lang,
            "title": self.scenario.title,
            "passed": self.passed,
            "failures": list(self.failures),
            "judge": {
                "overall": self.judge_overall,
                "scores": self.dimension_means(),
                "min_required": self.scenario.judge_min,
            },
            "session_id": self.session_id,
            "turns": [turn.to_dict() for turn in self.turns],
        }


# --- execution ---------------------------------------------------------------


def _tool_summary(tool_calls: list[dict[str, Any]]) -> str:
    """The judge's tool-call summary line (§3.2 judge input)."""
    if not tool_calls:
        return "(no tools called)"
    return "; ".join(
        f"{call['name']}({call.get('args_summary', '')})" for call in tool_calls
    )


async def run_scenario(
    scenario: Scenario,
    app: FastAPI,
    judge: Judge,
    capture: TurnLogCapture,
    catalog: CatalogAdapter | None = None,
) -> ScenarioResult:
    """Run one scripted scenario against an opened app (§2.3)."""
    catalog = catalog if catalog is not None else app.state.catalog
    session = ChatSession(app)
    result = ScenarioResult(
        scenario=scenario, session_id=session.session_id, user_id=session.user_id
    )
    audits: list[GroundingAudit] = []

    if scenario.profile:
        await seed_profile(app, session.user_id, scenario.profile)
    for item in scenario.seed_cart:
        await session.add_cart_item(
            item.product_id, quantity=item.quantity, variant=item.variant
        )

    actions_by_turn: dict[int, list[ScenarioAction]] = {}
    for action in scenario.actions:
        actions_by_turn.setdefault(action.after_turn, []).append(action)

    try:
        for index, turn in enumerate(scenario.turns):
            turn_result, audit = await _run_turn(
                turn, index, scenario, session, judge, capture, catalog
            )
            result.turns.append(turn_result)
            audits.append(audit)
            for failure in _turn_failures(turn_result, scenario.judge_min):
                result.failures.append(f"turn {index + 1}: {failure}")
            for action in actions_by_turn.get(index + 1, []):
                await _run_action(action, session)
                if action.type == "end_session":
                    # Deterministic personalization: the scheduled background
                    # extraction must be merged before the next turn's prompt.
                    await pending_extractions()
    finally:
        await session.aclose()

    merged = GroundingAudit()
    for audit in audits:
        merged = merged.merge(audit)
    result.grounding = merged
    result.judge_failed = any(turn.judge_error is not None for turn in result.turns)
    return result


async def _run_turn(
    turn: Turn,
    index: int,
    scenario: Scenario,
    session: ChatSession,
    judge: Judge,
    capture: TurnLogCapture,
    catalog: CatalogAdapter,
) -> tuple[TurnResult, GroundingAudit]:
    """One turn: SSE → turn log → judge → rule checks → cart assertion."""
    observation_raw = await session.send_turn(turn.user)
    record = capture.latest_turn(session.session_id) or {}

    # Executed tools (ground truth) from the turn log; SSE summaries are the
    # fallback when the record is missing.
    tool_hops = list(record.get("tool_hops") or [])
    tool_names = [hop.get("tool") for hop in tool_hops if hop.get("tool")]
    if not tool_names:
        tool_names = [call["name"] for call in observation_raw["tool_calls"]]

    turn_result = TurnResult(
        index=index,
        user=turn.user,
        reply=observation_raw["reply"],
        tool_calls=observation_raw["tool_calls"],
        tool_hops=tool_hops,
        cards=observation_raw["cards"],
        comparison=observation_raw["comparison"],
        cart_update=observation_raw["cart_update"],
        validation=record.get("validation"),
        error=observation_raw["error"],
    )

    # Cart API state when the turn declares a cart expectation (§2.3 "between
    # turns"): one fetch feeds both the rule check and the evidence record.
    cart_summary = (
        await session.cart_summary() if turn.expect.cart_expected is not None else None
    )

    # Judge pass (§3.2) — failure is recorded, never silent.
    card_ids = [str(card.get("product_id")) for card in turn_result.cards]
    excerpts = judge.catalog_excerpts(turn_result.reply, card_ids)
    judge_scores: dict[str, float] | None = None
    judge_clarifying: int | None = None
    try:
        verdict = await judge.score_turn(
            turn.user, turn_result.reply, _tool_summary(turn_result.tool_calls), excerpts
        )
        turn_result.judge = verdict.to_dict()
        judge_scores = verdict.scores
        judge_clarifying = verdict.clarifying_questions
    except JudgeError as exc:
        turn_result.judge_error = str(exc)

    # Rule checks (§3.1) — the judge-dependent clarifying cap sees the verdict.
    observation = TurnObservation(
        user=turn.user,
        reply=turn_result.reply,
        cards=turn_result.cards,
        comparison=turn_result.comparison,
        cart_update=turn_result.cart_update,
        tool_names=tool_names,
        validation=turn_result.validation,
        cart_summary=cart_summary,
        judge_scores=judge_scores,
        judge_clarifying=judge_clarifying,
        error=turn_result.error,
    )
    outcome: TurnCheckOutcome = check_turn(turn, observation, catalog, scenario.lang)
    turn_result.checks = outcome.to_dicts()
    turn_result.language = {
        "expected": outcome.language_expected,
        "detected": outcome.language_detected,
        "ok": any(c.ok for c in outcome.checks if c.name == "language"),
    }

    # Cart API assertion after the declaring turn (§2.3 "between turns").
    if turn.expect.cart_expected is not None:
        actual = cart_summary
        cart_check = next(c for c in outcome.checks if c.name == "cart")
        turn_result.cart_assert = {
            "ok": cart_check.ok,
            "expected": {
                "items": [
                    {
                        "product_id": item.product_id,
                        "quantity": item.quantity,
                        "variant": item.variant,
                    }
                    for item in turn.expect.cart_expected.items
                ],
                "item_count": turn.expect.cart_expected.item_count,
                "total": turn.expect.cart_expected.total,
            },
            "actual": actual,
            "detail": cart_check.detail,
        }

    turn_result.passed = outcome.passed and turn_result.judge_error is None
    return turn_result, outcome.grounding


def _turn_failures(turn_result: TurnResult, judge_min: float) -> list[str]:
    """Human-readable failure lines for one turn (§2.3 failure policy)."""
    failures: list[str] = []
    for check in turn_result.checks:
        if not check["ok"]:
            detail = f"{check['name']}: {check['detail']}" if check["detail"] else check["name"]
            failures.append(detail)
    if turn_result.judge_error is not None:
        failures.append(f"judge failed: {turn_result.judge_error}")
    if turn_result.judge is not None:
        for dim, value in turn_result.judge["scores"].items():
            if value < judge_min:
                failures.append(
                    f"judge dimension {dim} = {value:g} below scenario minimum {judge_min:g}"
                )
    return failures


async def _run_action(action: ScenarioAction, session: ChatSession) -> None:
    """One between-turn harness action (ACTION_TYPES)."""
    if action.type == "end_session":
        await session.end_session()
    elif action.type == "clear_profile":
        await session.clear_profile()
