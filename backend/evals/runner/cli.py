"""Eval runner CLI (Eval Plan §4): golden set → live app → report.

``uv run python -m evals.runner`` runs the full golden set. Modes:

- default (no ``--hermetic``): the configured provider facade drives Maya and
  a real judge pass scores every turn — this is the gate run (provider
  overridable via ``--provider/--model`` or ambient env settings);
- ``--hermetic``: the scripted chat model + scripted judge (zero provider
  contact) — the evidence run that proves harness correctness deterministically.

``--eval-compare`` merges the latest per-provider reports into one matrix.

Exit code follows the gate verdict (0 = pass, 1 = fail, 2 = harness error),
so CI can gate on it directly.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.gateway.client import create_chat_client
from evals.aggregate import build_report
from evals.judge.client import Judge, prompt_version, resolve_judge_settings
from evals.report import (
    REPORTS_DIR,
    latest_reports,
    write_html_report,
    write_json_report,
)
from evals.runner.capture import TurnLogCapture
from evals.runner.fake_model import HermeticScriptedModel, ScriptedJudge
from evals.runner.harness import eval_app_generator, run_scenario
from evals.schema import load_scenarios, verify_taxonomy
from evals.scenarios import GOLDEN_DIR


def _provider_tag(settings: Settings) -> tuple[str, str]:
    """(provider, model) actually in effect for this run."""
    provider = settings.llm_provider
    model = settings.ollama_chat_model if provider == "ollama" else settings.llm_model
    return provider, model


async def _run(
    args: argparse.Namespace,
    settings: Settings,
) -> dict[str, Any]:
    scenarios = load_scenarios(Path(GOLDEN_DIR))
    if args.limit:
        scenarios = scenarios[: args.limit]
    if args.filter:
        scenarios = [s for s in scenarios if args.filter in s.id]
    taxonomy_problems = verify_taxonomy(load_scenarios(Path(GOLDEN_DIR)))

    started = time.monotonic()
    results = []
    with TurnLogCapture() as capture:
        for scenario in scenarios:
            if args.hermetic:
                # Fresh app per scenario: total isolation between scripted
                # carts/profiles, and the scripted model derives its plan
                # from this scenario's own catalog view.
                async with eval_app_generator(settings=_hermetic_settings(settings)) as app:
                    model = HermeticScriptedModel(scenario, app.state.catalog)
                    results.append(
                        await run_scenario(
                            scenario, app, ScriptedJudge(), capture, catalog=app.state.catalog
                        )
                    )
            else:
                async with eval_app_generator(settings=_live_settings(settings)) as app:
                    judge = Judge(create_chat_client(resolve_judge_settings(settings)), app.state.catalog)
                    results.append(await run_scenario(scenario, app, judge, capture))
    duration = time.monotonic() - started

    provider, model = _provider_tag(settings)
    judge_settings = resolve_judge_settings(settings)
    judge_provider, judge_model = _provider_tag(judge_settings)
    notes_bits = []
    if args.limit or args.filter:
        notes_bits.append(
            f"partial run: limit={args.limit or '-'} filter={args.filter or '-'}"
        )
    report = build_report(
        results,
        provider=provider,
        model=model,
        judge_provider=judge_provider,
        judge_model=judge_model,
        judge_prompt_version=prompt_version(),
        hermetic=args.hermetic,
        duration_s=duration,
        notes="; ".join(notes_bits),
        taxonomy_problems=taxonomy_problems,
    )
    return report


def _hermetic_settings(settings: Settings) -> Settings:
    """Settings for the hermetic evidence app (env file ignored upstream)."""
    return Settings(
        _env_file=None,
        database_url=settings.database_url,
        checkpoint_db_url=settings.checkpoint_db_url,
        embedding_provider="none",
    )


def _live_settings(settings: Settings) -> Settings:
    """Ambient provider settings with throwaway storage paths."""
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="shopmate-eval-live-"))
    return settings.model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{tmp}/shopmate.db",
            "checkpoint_db_url": f"sqlite+aiosqlite:///{tmp}/checkpoints.db",
        }
    )


def _compare() -> int:
    """Merge the latest per-provider reports into one comparison matrix."""
    import json

    latest = latest_reports()
    if len(latest) < 2:
        print(
            "eval-compare needs reports from >= 2 providers; run "
            "`make eval PROVIDER=...` per provider first.",
            file=sys.stderr,
        )
        return 2
    rows: list[dict[str, Any]] = []
    for label, path in sorted(latest.items()):
        report = json.loads(path.read_text(encoding="utf-8"))
        rows.append(
            {
                "provider": label,
                "verdict": report["gate"]["verdict"],
                "judge_overall": report["gate"]["judge_overall"],
                "scenarios_passed": (
                    f"{report['meta'].get('passed_count', '?')}"
                    f"/{report['meta'].get('scenario_count', '?')}"
                ),
                **{name: entry["value"] for name, entry in report["metrics"].items()},
            }
        )
    out = REPORTS_DIR / "comparison.json"
    out.write_text(json.dumps({"schema": "shopmate-eval-compare/1", "runs": rows}, indent=1), encoding="utf-8")
    headers = ["provider", "verdict", "judge_overall", "scenarios_passed", *sorted(
        {k for row in rows for k in row if k not in ("provider", "verdict", "judge_overall", "scenarios_passed")}
    )]
    print(f"{' | '.join(headers)}")
    for row in rows:
        print(" | ".join(
            f"{row.get(h):.3f}" if isinstance(row.get(h), float) else str(row.get(h))
            for h in headers
        ))
    print(f"\nmatrix written to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.runner", description=__doc__)
    parser.add_argument("--hermetic", action="store_true", help="scripted model + judge (no provider)")
    parser.add_argument("--limit", type=int, default=None, help="first N scenarios only")
    parser.add_argument("--filter", default=None, help="run scenarios whose id contains this substring")
    parser.add_argument("--no-report", action="store_true", help="print summary only; skip report files")
    parser.add_argument(
        "--eval-compare", action="store_true", help="merge latest per-provider reports into a matrix"
    )
    args = parser.parse_args(argv)
    if args.eval_compare:
        return _compare()

    settings = get_settings()
    report = asyncio.run(_run(args, settings))

    gate = report["gate"]
    meta = report["meta"]
    print(
        f"\n{'HERMETIC' if meta['hermetic'] else 'LIVE'} run "
        f"{meta['provider']}/{meta['model']} — {meta['passed_count']}/{meta['scenario_count']} "
        f"scenarios passed in {meta['duration_s']}s"
    )
    for check in gate["checks"]:
        status = "PASS" if check["ok"] else "FAIL"
        value = check.get("value")
        value_s = "n/a" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)
        threshold = check.get("threshold")
        print(
            f"  [{status}] {check['metric']}"
            + (f" ({value_s} {check.get('op', '')} {threshold})" if threshold is not None else "")
        )
    print(f"gate verdict: {gate['verdict'].upper()}")
    if report["meta"]["taxonomy_problems"]:
        print("taxonomy problems:", *report["meta"]["taxonomy_problems"], sep="\n  - ")
    if not args.no_report:
        json_path = write_json_report(report)
        html_path = write_html_report(report)
        print(f"reports: {json_path}\n         {html_path}")
    return 0 if gate["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
