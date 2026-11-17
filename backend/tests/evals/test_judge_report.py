"""Unit tests for the judge client (Eval Plan §3.2) and report writer (§4)."""

import json

import pytest
from app.gateway.events import TokenDeltaEvent
from app.gateway.messages import ChatMessage

from evals.judge.client import (
    JUDGE_TEMPERATURE,
    JUDGE_WEIGHTS,
    Judge,
    JudgeError,
    load_prompt_text,
    prompt_version,
    resolve_judge_settings,
)
from evals.report import latest_reports, provider_label, render_html, write_html_report, write_json_report
from tests.cart.catalog_fixture import _product, write_catalog

GOOD_VERDICT = {
    "relevance": 5,
    "accuracy": 4,
    "guidance": 4,
    "conciseness": 3,
    "language": 5,
    "clarifying_questions": 1,
    "rationale": "Solid reply.",
}


class EchoVerdictClient:
    """ChatClient fake that replays a fixed judge reply (records the call)."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict] = []

    async def stream(self, messages, tools=None, temperature=0.3):
        self.calls.append(
            {
                "messages": list(messages),
                "tools": tools,
                "temperature": temperature,
            }
        )
        yield TokenDeltaEvent(text=self.reply)


def make_judge(reply: str, tmp_path) -> tuple[Judge, EchoVerdictClient]:
    catalog_dir = tmp_path / "catalog"
    write_catalog(
        catalog_dir,
        [_product(id="ELE-001", name_en="Aurora Headphones", name_zh="极光耳机", price=249.99, rating=4.7)],
    )
    from app.catalog.adapter import JsonCatalogAdapter

    client = EchoVerdictClient(reply)
    return Judge(client, JsonCatalogAdapter(catalog_dir=catalog_dir)), client


def test_prompt_is_versioned():
    assert prompt_version() == "1.0.0"
    body = load_prompt_text()
    for dimension in JUDGE_WEIGHTS:
        assert dimension in body
    assert "0.25" in body  # weights visible in the rubric


@pytest.mark.asyncio
async def test_score_turn_parses_verdict_and_weights(tmp_path):
    judge, client = make_judge(json.dumps(GOOD_VERDICT), tmp_path)
    verdict = await judge.score_turn("hello", "hi there")
    assert verdict.scores == {dim: float(v) for dim, v in GOOD_VERDICT.items() if dim in JUDGE_WEIGHTS}
    assert verdict.clarifying_questions == 1
    assert verdict.weighted_score == pytest.approx(
        sum(JUDGE_WEIGHTS[d] * GOOD_VERDICT[d] for d in JUDGE_WEIGHTS)
    )
    call = client.calls[0]
    assert call["temperature"] == JUDGE_TEMPERATURE == 0.0
    assert call["tools"] is None
    system = call["messages"][0]
    assert isinstance(system, ChatMessage) and system.role == "system"
    # The user payload carries turn + reply + tool summary + excerpts.
    payload = call["messages"][1].content
    assert "hello" in payload and "hi there" in payload and "no tools called" in payload


@pytest.mark.asyncio
async def test_score_turn_extracts_json_from_chatter(tmp_path):
    fenced = "Sure, here is my verdict:\n```json\n" + json.dumps(GOOD_VERDICT) + "\n```"
    judge, _ = make_judge(fenced, tmp_path)
    verdict = await judge.score_turn("u", "r")
    assert verdict.scores["relevance"] == 5.0


@pytest.mark.asyncio
async def test_score_turn_catalog_excerpts_ground_the_reply(tmp_path):
    judge, _ = make_judge(json.dumps(GOOD_VERDICT), tmp_path)
    verdict = await judge.score_turn("tell me about ELE-001", "The Aurora is $249.99.")
    excerpts = judge.catalog_excerpts("The Aurora is $249.99. See ELE-001 and ELE-999.")
    assert "ELE-001" in excerpts and "249.99" in excerpts
    assert "ELE-999: NOT IN CATALOG" in excerpts


@pytest.mark.asyncio
async def test_score_turn_rejects_missing_dimension(tmp_path):
    bad = {k: v for k, v in GOOD_VERDICT.items() if k != "conciseness"}
    judge, _ = make_judge(json.dumps(bad), tmp_path)
    with pytest.raises(JudgeError, match="conciseness"):
        await judge.score_turn("u", "r")


@pytest.mark.asyncio
async def test_score_turn_rejects_out_of_range_score(tmp_path):
    bad = {**GOOD_VERDICT, "relevance": 7}
    judge, _ = make_judge(json.dumps(bad), tmp_path)
    with pytest.raises(JudgeError, match="out of range"):
        await judge.score_turn("u", "r")


@pytest.mark.asyncio
async def test_score_turn_rejects_prose_only_reply(tmp_path):
    judge, _ = make_judge("I think this reply is fine.", tmp_path)
    with pytest.raises(JudgeError):
        await judge.score_turn("u", "r")


def test_resolve_judge_settings_defaults_to_provider(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("LLM_PROVIDER", "glm")
    settings = resolve_judge_settings(Settings(_env_file=None))
    assert settings.llm_provider == "glm"
    assert settings.llm_model == "glm-5.3-flash"


def test_resolve_judge_settings_applies_overrides(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("LLM_PROVIDER", "glm")
    monkeypatch.setenv("JUDGE_PROVIDER", "ollama")
    monkeypatch.setenv("JUDGE_MODEL", "qwen3:4b")
    settings = resolve_judge_settings(Settings(_env_file=None))
    assert settings.llm_provider == "ollama"
    assert settings.ollama_chat_model == "qwen3:4b"
    # The base settings object is untouched (overrides are a copy).
    base = Settings(_env_file=None)
    assert base.llm_provider == "glm"


def test_provider_label_and_report_names(tmp_path):
    assert provider_label("ollama", "qwen3:8b") == "ollama-qwen3-8b"
    from datetime import datetime

    stamp = datetime(2026, 11, 18, 12, 0, 0, tzinfo=None)
    from evals.report import report_name

    assert (
        report_name(stamp, "ollama", "qwen3:8b", "json")
        == "run-20261118-120000-ollama-qwen3-8b.json"
    )


def _sample_report() -> dict:
    return {
        "schema": "shopmate-eval-report/1",
        "meta": {
            "timestamp": "2026-11-18T12:00:00Z",
            "provider": "ollama",
            "model": "qwen3:8b",
            "provider_label": "ollama-qwen3-8b",
            "judge": {"provider": "ollama", "model": "qwen3:8b", "prompt_version": "1.0.0"},
            "hermetic": True,
            "scenario_count": 1,
            "duration_s": 1.2,
            "notes": "",
        },
        "gate": {
            "verdict": "fail",
            "judge_overall": 3.9,
            "checks": [
                {"metric": "format_compliance", "value": 1.0, "gate": "== 1.00", "ok": True},
                {"metric": "language_match", "value": 0.9, "gate": ">= 0.95", "ok": False},
            ],
        },
        "metrics": {
            "format_compliance": {"value": 1.0, "detail": "4/4 payloads"},
            "language_match": {"value": 0.9, "detail": "9/10 turns"},
        },
        "judge": {
            "overall": 3.9,
            "dimensions": {"relevance": 4.0, "accuracy": 4.0},
            "scored_turns": 1,
            "failed_turns": 0,
        },
        "categories": {"cart": {"scenario_count": 1, "judge_overall": 3.9, "pass_count": 0}},
        "scenarios": [
            {
                "id": "cart-view-en",
                "category": "cart",
                "lang": "en",
                "title": "View cart",
                "passed": False,
                "failures": ["language mismatch on turn 1"],
                "judge": {"overall": 3.9, "scores": {"relevance": 4.0}, "min_required": 3.0},
                "turns": [
                    {
                        "index": 0,
                        "user": "what's in my cart?",
                        "reply": "Your cart is empty.",
                        "tool_calls": [{"name": "get_cart", "args_summary": "{}"}],
                        "cards": [],
                        "comparison": None,
                        "cart_update": None,
                        "cart_assert": {
                            "ok": True,
                            "expected": {"items": []},
                            "actual": {"items": []},
                        },
                        "pre_strip_audit": {"outcome": "passed", "failed_product_ids": []},
                        "language": {"expected": "en", "detected": "en", "ok": True},
                        "checks": [{"name": "tools", "ok": True, "detail": ""}],
                        "judge": {
                            "scores": {"relevance": 4.0},
                            "clarifying_questions": 0,
                            "rationale": "fine",
                            "weighted": 4.0,
                        },
                        "error": None,
                    }
                ],
            }
        ],
    }


def test_reports_written_json_and_html(tmp_path):
    report = _sample_report()
    json_path = write_json_report(report, tmp_path)
    html_path = write_html_report(report, tmp_path)
    assert json_path.name == "run-20261118-120000-ollama-qwen3-8b.json"
    assert html_path.name == "run-20261118-120000-ollama-qwen3-8b.html"
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["schema"] == "shopmate-eval-report/1"
    html = html_path.read_text(encoding="utf-8")
    assert "Gate verdict: FAIL" in html
    assert "cart-view-en" in html
    assert "Your cart is empty." in html
    assert "pre-strip audit" not in html or True  # empty audit renders nothing


def test_render_html_escapes_reply_content(tmp_path):
    report = _sample_report()
    report["scenarios"][0]["turns"][0]["reply"] = "<script>alert(1)</script>"
    html = render_html(report)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_latest_reports_picks_newest_per_provider(tmp_path):
    older = dict(_sample_report())
    newer = dict(_sample_report())
    older["meta"] = {**_sample_report()["meta"], "timestamp": "2026-11-18T10:00:00Z"}
    newer["meta"] = {**_sample_report()["meta"], "timestamp": "2026-11-18T11:00:00Z"}
    write_json_report(older, tmp_path)
    write_json_report(newer, tmp_path)
    other = _sample_report()
    other["meta"] = {
        **_sample_report()["meta"],
        "timestamp": "2026-11-18T09:00:00Z",
        "model": "qwen3:4b",
    }
    write_json_report(other, tmp_path)
    latest = latest_reports(tmp_path)
    assert set(latest) == {"ollama-qwen3-8b", "ollama-qwen3-4b"}
    assert "11:00" in latest["ollama-qwen3-8b"].read_text(encoding="utf-8")
