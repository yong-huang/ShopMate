"""Run aggregation + gate verdict (Eval Plan §3.1/§3.3) + report assembly.

Produces the report dict consumed by :mod:`evals.report` (shape documented
there). Metrics follow §3.1 exactly:

- ``product_id_grounding_rate`` — pooled over every scenario's grounding
  audit (rendered + pre-strip ids); ``None`` when nothing was measured, in
  which case the gate treats it as not-applicable (no cards anywhere would
  mean the set never exercises commerce rendering — a taxonomy violation the
  loader/taxonomy check catches separately).
- ``tool_selection_accuracy`` — scenarios with tool expectations whose every
  turn's ``tools`` check passed ÷ scenarios with tool expectations.
- ``cart_correctness`` — scenarios with cart expectations whose every cart
  check passed ÷ scenarios with cart expectations.
- ``language_match`` — per-turn ``language`` checks ok ÷ turns.
- ``format_compliance`` — ``format`` checks ok ÷ turns with structured
  payloads (cards or a comparison event).
- judge overall — weighted mean over all scored turns (§3.2 weights via
  ``turn.judge["weighted"]``), plus per-dimension means.

Gate (§3.3): all §3.1 thresholds pass AND judge overall ≥ 4.0/5.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from evals.report import GATES, JUDGE_GATE
from evals.runner.harness import ScenarioResult


def _ratio(ok: int, total: int) -> float | None:
    return ok / total if total else None


def _metric_entry(value: float | None, ok_count: int, total: int, detail: str) -> dict[str, Any]:
    return {
        "value": value,
        "ok_count": ok_count,
        "total": total,
        "detail": detail,
        "measured": total > 0,
    }


def _gate_check(metric: str, entry: dict[str, Any]) -> dict[str, Any]:
    op, threshold = GATES[metric]
    if not entry["measured"]:
        return {"metric": metric, "ok": True, "note": "not measured (no relevant turns)"}
    value = entry["value"]
    ok = value >= threshold if op == "min" else value == threshold
    return {"metric": metric, "op": op, "threshold": threshold, "value": value, "ok": ok}


def _scenario_passed_checks(result: ScenarioResult, check_name: str) -> bool:
    """True when every check with this name in the scenario passed.

    Turns whose checks lack the name (nothing asserted) don't count against
    it — but a scenario whose relevant check failed anywhere fails here.
    """
    relevant = [
        check
        for turn in result.turns
        for check in turn.checks
        if check["name"] == check_name
    ]
    return all(check["ok"] for check in relevant) if relevant else False


def _scenario_has_expectation(result: ScenarioResult, check_name: str) -> bool:
    return any(
        check["name"] == check_name for turn in result.turns for check in turn.checks
    )


def build_report(
    results: list[ScenarioResult],
    *,
    provider: str,
    model: str,
    judge_provider: str,
    judge_model: str,
    judge_prompt_version: str,
    hermetic: bool,
    duration_s: float,
    notes: str = "",
    taxonomy_problems: list[str] | None = None,
) -> dict[str, Any]:
    """Aggregate scenario results into the report dict (§3.1/§3.3)."""
    # --- §3.1 metrics ---------------------------------------------------------
    rendered = sum(len(r.grounding.rendered_card_ids) for r in results)
    pre_strip = sum(len(r.grounding.pre_strip_ids) for r in results)
    grounding_ok = rendered - sum(len(r.grounding.fabricated_rendered) for r in results) + (
        pre_strip - sum(len(r.grounding.fabricated_pre_strip) for r in results)
    )
    grounding = _metric_entry(
        _ratio(grounding_ok, rendered + pre_strip),
        grounding_ok,
        rendered + pre_strip,
        f"rendered={rendered}, pre-strip={pre_strip}",
    )
    hard_fail = any(r.grounding.hard_fail for r in results)

    tool_scenarios = [r for r in results if _scenario_has_expectation(r, "tools")]
    tool_ok = sum(_scenario_passed_checks(r, "tools") for r in tool_scenarios)
    tools = _metric_entry(
        _ratio(tool_ok, len(tool_scenarios)),
        tool_ok,
        len(tool_scenarios),
        "scenario-level: all tool-subset checks pass",
    )

    cart_scenarios = [r for r in results if _scenario_has_expectation(r, "cart")]
    cart_ok = sum(_scenario_passed_checks(r, "cart") for r in cart_scenarios)
    cart = _metric_entry(
        _ratio(cart_ok, len(cart_scenarios)),
        cart_ok,
        len(cart_scenarios),
        "scenario-level: all cart checks pass",
    )

    lang_checks = [
        check for r in results for turn in r.turns for check in turn.checks
        if check["name"] == "language"
    ]
    lang_ok = sum(check["ok"] for check in lang_checks)
    language = _metric_entry(
        _ratio(lang_ok, len(lang_checks)),
        lang_ok,
        len(lang_checks),
        "per-turn CJK-ratio match",
    )

    format_checks = [
        check for r in results for turn in r.turns for check in turn.checks
        if check["name"] == "format"
    ]
    format_ok = sum(check["ok"] for check in format_checks)
    format_c = _metric_entry(
        _ratio(format_ok, len(format_checks)),
        format_ok,
        len(format_checks),
        "card/comparison payload schema compliance",
    )

    metrics = {
        "product_id_grounding_rate": grounding,
        "tool_selection_accuracy": tools,
        "cart_correctness": cart,
        "language_match": language,
        "format_compliance": format_c,
    }

    # --- judge aggregation ------------------------------------------------------
    weighted_scores = [
        turn.judge["weighted"]
        for r in results
        for turn in r.turns
        if turn.judge is not None
    ]
    dimension_scores: dict[str, list[float]] = {}
    for r in results:
        for turn in r.turns:
            if turn.judge is not None:
                for dim, value in turn.judge["scores"].items():
                    dimension_scores.setdefault(dim, []).append(value)
    judge_overall = (
        sum(weighted_scores) / len(weighted_scores) if weighted_scores else None
    )
    judge_block = {
        "overall": judge_overall,
        "dimensions": {
            dim: sum(vals) / len(vals) for dim, vals in sorted(dimension_scores.items())
        },
        "scored_turns": len(weighted_scores),
        "failed_turns": sum(
            1
            for r in results
            for turn in r.turns
            if turn.judge_error is not None
        ),
    }

    # --- categories ---------------------------------------------------------
    categories: dict[str, dict[str, Any]] = {}
    for r in results:
        cat = categories.setdefault(
            r.scenario.category,
            {"scenario_count": 0, "pass_count": 0, "judge_scores": []},
        )
        cat["scenario_count"] += 1
        if r.passed:
            cat["pass_count"] += 1
        if r.judge_overall is not None:
            cat["judge_scores"].append(r.judge_overall)
    for cat in categories.values():
        scores = cat.pop("judge_scores")
        cat["judge_overall"] = sum(scores) / len(scores) if scores else None

    # --- gate (§3.3) ---------------------------------------------------------
    gate_checks = [_gate_check(metric, entry) for metric, entry in metrics.items()]
    judge_check = {
        "metric": "judge_overall",
        "op": "min",
        "threshold": JUDGE_GATE,
        "value": judge_overall,
        "ok": judge_overall is not None and judge_overall >= JUDGE_GATE,
    }
    gate_checks.append(judge_check)
    # Any rendered fabricated id is a hard fail regardless of the rate (§3.1).
    if hard_fail:
        gate_checks.append(
            {"metric": "grounding_hard_fail", "ok": False, "note": "rendered fabricated product id"}
        )
    verdict = "pass" if all(check["ok"] for check in gate_checks) else "fail"

    passed = sum(1 for r in results if r.passed)
    report: dict[str, Any] = {
        "schema": "shopmate-eval-report/1",
        "meta": {
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "provider": provider,
            "model": model,
            "provider_label": f"{provider}-{model}",
            "judge": {
                "provider": judge_provider,
                "model": judge_model,
                "prompt_version": judge_prompt_version,
            },
            "hermetic": hermetic,
            "scenario_count": len(results),
            "passed_count": passed,
            "duration_s": round(duration_s, 2),
            "notes": notes,
            "taxonomy_problems": taxonomy_problems or [],
        },
        "gate": {
            "verdict": verdict,
            "checks": gate_checks,
            "judge_overall": judge_overall,
        },
        "metrics": metrics,
        "judge": judge_block,
        "categories": categories,
        "scenarios": [r.to_dict() for r in results],
    }
    return report
