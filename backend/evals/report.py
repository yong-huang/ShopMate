"""Run-report writing (Eval Plan §4): JSON + HTML under ``evals/reports/``.

Filenames follow ``run-<timestamp>-<provider>.json`` / ``.html`` where
``<timestamp>`` is ``YYYYMMDD-HHMMSS`` (UTC) and ``<provider>`` is the
sanitized ``provider-model`` label. The HTML rendering is self-contained
(inline CSS, no JS) so reports attach to tickets directly.

Report dict shape (consumed here, produced by :mod:`evals.aggregate`)::

    {
      "schema": "shopmate-eval-report/1",
      "meta": {"timestamp", "provider", "model", "provider_label",
               "judge": {"provider", "model", "prompt_version"},
               "hermetic", "scenario_count", "duration_s", "notes"},
      "gate": {"verdict": "pass"|"fail", "checks": [...], "judge_overall"},
      "metrics": {"product_id_grounding_rate": {...}, ...one per §3.1 metric},
      "judge": {"overall", "dimensions", "scored_turns", "failed_turns"},
      "categories": {category: {"scenario_count", "judge_overall", "pass_count"}},
      "scenarios": [ScenarioReport...],
    }

Each scenario report carries the full evidence chain per §4: transcript,
tool-call log, card events (incl. the pre-strip grounding audit), cart
assertions, judge dimension scores, and per-turn rule-check results.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from html import escape
from pathlib import Path

REPORTS_DIR = Path(__file__).parent / "reports"

REPORT_SCHEMA = "shopmate-eval-report/1"

# §3.1 gates: metric -> (op, threshold). "min" = value >= threshold,
# "exact" = value == threshold (100%).
GATES: dict[str, tuple[str, float]] = {
    "product_id_grounding_rate": ("min", 0.99),
    "tool_selection_accuracy": ("min", 0.90),
    "cart_correctness": ("min", 0.95),
    "language_match": ("min", 0.95),
    "format_compliance": ("exact", 1.00),
}

JUDGE_GATE = 4.0  # §3.3: judge overall >= 4.0/5

METRIC_TITLES: dict[str, str] = {
    "product_id_grounding_rate": "Product-ID grounding rate",
    "tool_selection_accuracy": "Tool-selection accuracy",
    "cart_correctness": "Cart correctness",
    "language_match": "Language match",
    "format_compliance": "Format compliance",
}


def provider_label(provider: str, model: str) -> str:
    """Report filename label: sanitized ``provider-model`` (e.g. ollama-qwen3-8b)."""
    raw = f"{provider}-{model}" if model else provider
    return "".join(ch if ch.isalnum() else "-" for ch in raw.lower()).strip("-")


def report_name(timestamp: datetime, provider: str, model: str, suffix: str) -> str:
    """``run-<timestamp>-<provider>.<suffix>`` per Eval Plan §4."""
    stamp = timestamp.strftime("%Y%m%d-%H%M%S")
    return f"run-{stamp}-{provider_label(provider, model)}.{suffix}"


def write_json_report(report: dict, reports_dir: Path | None = None) -> Path:
    """Write the JSON report; returns the path."""
    out_dir = Path(reports_dir) if reports_dir is not None else REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.strptime(report["meta"]["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
    path = out_dir / report_name(timestamp, report["meta"]["provider"], report["meta"]["model"], "json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def write_html_report(report: dict, reports_dir: Path | None = None) -> Path:
    """Write the HTML summary; returns the path."""
    out_dir = Path(reports_dir) if reports_dir is not None else REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.strptime(report["meta"]["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
    path = out_dir / report_name(timestamp, report["meta"]["provider"], report["meta"]["model"], "html")
    path.write_text(render_html(report), encoding="utf-8")
    return path


def latest_reports(reports_dir: Path | None = None) -> dict[str, Path]:
    """The newest JSON report per provider label (for ``eval-compare``).

    Returns ``{provider_label: path}``; a provider label is the ``run-...``
    filename part after the timestamp.
    """
    out_dir = Path(reports_dir) if reports_dir is not None else REPORTS_DIR
    latest: dict[str, tuple[str, Path]] = {}
    for path in sorted(out_dir.glob("run-*.json")):
        stem = path.stem  # run-<timestamp>-<provider>
        parts = stem.split("-")
        if len(parts) < 4:
            continue
        stamp, label = f"{parts[1]}-{parts[2]}", "-".join(parts[3:])
        current = latest.get(label)
        if current is None or stamp > current[0]:
            latest[label] = (stamp, path)
    return {label: path for label, (_, path) in latest.items()}


# --- HTML rendering ---------------------------------------------------------


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2%}" if value <= 1.0 else f"{value:.2f}"


def _esc(value: object) -> str:
    return escape(str(value))


def _render_gate(report: dict) -> str:
    gate = report.get("gate", {})
    verdict = gate.get("verdict", "unknown")
    cls = "pass" if verdict == "pass" else "fail"
    rows = []
    for check in gate.get("checks", []):
        rows.append(
            "<tr>"
            f"<td>{_esc(check.get('metric'))}</td>"
            f"<td class='num'>{_fmt(check.get('value'))}</td>"
            f"<td class='num'>{_esc(check.get('gate'))}</td>"
            f"<td class='{'pass' if check.get('ok') else 'fail'}'>"
            f"{'ok' if check.get('ok') else 'FAIL'}</td>"
            "</tr>"
        )
    return (
        f"<div class='verdict {cls}'>Gate verdict: {_esc(verdict).upper()}</div>"
        "<table><tr><th>Metric</th><th>Value</th><th>Gate</th><th>Result</th></tr>"
        f"{''.join(rows)}</table>"
    )


def _render_metrics(report: dict) -> str:
    metrics = report.get("metrics", {})
    rows = []
    for name, title in METRIC_TITLES.items():
        metric = metrics.get(name, {})
        detail = metric.get("detail") or ""
        rows.append(
            "<tr>"
            f"<td>{_esc(title)}</td>"
            f"<td class='num'>{_fmt(metric.get('value'))}</td>"
            f"<td>{_esc(detail)}</td>"
            "</tr>"
        )
    judge = report.get("judge", {})
    rows.append(
        "<tr><td>Judge overall (weighted, §3.2)</td>"
        f"<td class='num'>{_fmt(judge.get('overall'))}</td>"
        f"<td>scored_turns={_esc(judge.get('scored_turns'))} failed_turns={_esc(judge.get('failed_turns'))}</td></tr>"
    )
    return (
        "<table><tr><th>Metric</th><th>Value</th><th>Detail</th></tr>"
        f"{''.join(rows)}</table>"
    )


def _render_categories(report: dict) -> str:
    rows = []
    for category, data in report.get("categories", {}).items():
        rows.append(
            "<tr>"
            f"<td>{_esc(category)}</td>"
            f"<td class='num'>{_esc(data.get('scenario_count'))}</td>"
            f"<td class='num'>{_esc(data.get('pass_count'))}</td>"
            f"<td class='num'>{_fmt(data.get('judge_overall'))}</td>"
            "</tr>"
        )
    return (
        "<table><tr><th>Category</th><th>Scenarios</th><th>Passed</th>"
        "<th>Judge overall</th></tr>" + "".join(rows) + "</table>"
    )


def _render_scenario(scenario: dict) -> str:
    cls = "pass" if scenario.get("passed") else "fail"
    head = (
        f"<h3 class='{'pass' if scenario.get('passed') else 'fail'}'>"
        f"{_esc(scenario.get('id'))} <small>({_esc(scenario.get('category'))} / "
        f"{_esc(scenario.get('lang'))}) — {'PASS' if scenario.get('passed') else 'FAIL'}</small></h3>"
    )
    if scenario.get("title"):
        head += f"<p><em>{_esc(scenario.get('title'))}</em></p>"
    parts = [head]
    failures = scenario.get("failures") or []
    if failures:
        items = "".join(f"<li>{_esc(failure)}</li>" for failure in failures)
        parts.append(f"<ul class='failures'>{items}</ul>")
    judge = scenario.get("judge") or {}
    if judge:
        dims = ", ".join(f"{dim}={value:g}" for dim, value in (judge.get("scores") or {}).items())
        parts.append(
            f"<p>judge overall={judge.get('overall'):g} ({_esc(dims)}; "
            f"min required={_esc(judge.get('min_required'))})</p>"
        )
    for index, turn in enumerate(scenario.get("turns", [])):
        parts.append(f"<h4>Turn {index + 1}</h4>")
        parts.append(f"<p class='user'>user: {_esc(turn.get('user'))}</p>")
        parts.append(f"<p class='reply'>maya: {_esc(turn.get('reply'))}</p>")
        tools = turn.get("tool_calls") or []
        if tools:
            rendered = "; ".join(
                f"{_esc(tool.get('name'))}({_esc(tool.get('args_summary'))})" for tool in tools
            )
            parts.append(f"<p>tools: {rendered}</p>")
        cards = turn.get("cards") or []
        if cards:
            rendered = "; ".join(
                f"{_esc(card.get('product_id'))} ${_esc(card.get('price'))}" for card in cards
            )
            parts.append(f"<p>cards: {rendered}</p>")
        audit = turn.get("pre_strip_audit")
        if audit is not None and audit.get("failed_product_ids"):
            parts.append(
                f"<p class='fail'>pre-strip audit: stripped card ids "
                f"{_esc(audit.get('failed_product_ids'))} (outcome={_esc(audit.get('outcome'))})</p>"
            )
        cart = turn.get("cart_assert")
        if cart is not None:
            parts.append(
                f"<p class='{'pass' if cart.get('ok') else 'fail'}'>cart assert "
                f"(ok={_esc(cart.get('ok'))}): expected={_esc(json.dumps(cart.get('expected'), ensure_ascii=False))} "
                f"actual={_esc(json.dumps(cart.get('actual'), ensure_ascii=False))}</p>"
            )
        checks = turn.get("checks") or []
        if checks:
            rendered = " ".join(
                f"<span class='{'pass' if check.get('ok') else 'fail'}'>"
                f"{_esc(check.get('name'))}{'/ok' if check.get('ok') else ': ' + _esc(check.get('detail'))}"
                f"</span>"
                for check in checks
            )
            parts.append(f"<p class='checks'>{rendered}</p>")
        turn_judge = turn.get("judge")
        if turn_judge:
            dims = ", ".join(f"{dim}={value:g}" for dim, value in (turn_judge.get("scores") or {}).items())
            parts.append(
                f"<p class='judge'>judge: {dims} clarifying={_esc(turn_judge.get('clarifying_questions'))} "
                f"weighted={turn_judge.get('weighted'):g} — {_esc(turn_judge.get('rationale'))}</p>"
            )
        if turn.get("error"):
            parts.append(f"<p class='fail'>error: {_esc(turn.get('error'))}</p>")
    return "<section class='scenario'>" + "".join(parts) + "</section>"


def render_html(report: dict) -> str:
    """Render the report dict as a self-contained HTML page."""
    meta = report.get("meta", {})
    scenarios = "".join(_render_scenario(s) for s in report.get("scenarios", []))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ShopMate eval run — {_esc(meta.get('provider_label'))}</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", sans-serif; margin: 2rem auto; max-width: 62rem; color: #1a1a2e; }}
h1 {{ margin-bottom: 0; }}
h3 small {{ font-weight: normal; color: #555; }}
h4 {{ margin-bottom: 0.2rem; }}
table {{ border-collapse: collapse; margin: 0.6rem 0; }}
th, td {{ border: 1px solid #ccc; padding: 0.25rem 0.6rem; font-size: 0.9rem; text-align: left; }}
td.num {{ text-align: right; }}
.verdict {{ font-size: 1.2rem; font-weight: bold; padding: 0.5rem 1rem; border-radius: 6px; margin: 1rem 0; }}
.verdict.pass {{ background: #d8f3dc; color: #1b4332; }}
.verdict.fail {{ background: #ffdde1; color: #7f1d1d; }}
.pass {{ color: #1b7a3d; }}
.fail {{ color: #b91c1c; }}
.failures {{ color: #b91c1c; }}
section.scenario {{ border-top: 2px solid #eee; padding-top: 0.6rem; margin-top: 1rem; }}
p.user {{ background: #eef2ff; padding: 0.3rem 0.6rem; border-radius: 4px; }}
p.reply {{ background: #f0fdf4; padding: 0.3rem 0.6rem; border-radius: 4px; }}
p.checks span {{ margin-right: 0.8rem; font-size: 0.85rem; }}
code, pre {{ background: #f5f5f7; font-size: 0.85rem; }}
header p {{ color: #555; font-size: 0.9rem; }}
</style>
</head>
<body>
<h1>ShopMate eval run</h1>
<header>
<p>provider={_esc(meta.get('provider'))} model={_esc(meta.get('model'))}
 | judge={_esc(meta.get('judge', {}).get('provider'))}/{_esc(meta.get('judge', {}).get('model'))}
 rubric v{_esc(meta.get('judge', {}).get('prompt_version'))}
 | timestamp={_esc(meta.get('timestamp'))}
 | scenarios={_esc(meta.get('scenario_count'))}
 | hermetic={_esc(meta.get('hermetic'))}
 | duration={_esc(meta.get('duration_s'))}s</p>
</header>
{_render_gate(report)}
<h2>Rule metrics (§3.1)</h2>
{_render_metrics(report)}
<h2>Judge dimensions</h2>
<table><tr><th>Dimension</th><th>Mean</th></tr>
{''.join(f"<tr><td>{_esc(dim)}</td><td class='num'>{value:.2f}</td></tr>" for dim, value in (report.get('judge', {}).get('dimensions') or {}).items())}
</table>
<h2>Per-category breakdown</h2>
{_render_categories(report)}
<h2>Scenarios ({len(report.get('scenarios', []))})</h2>
{scenarios}
</body>
</html>
"""
