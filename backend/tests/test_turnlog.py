"""Tests for the per-turn logging context helper (NFR-4).

These exercise the public API a chat endpoint will reuse: `begin_turn`,
`current_turn`, the `record_*` hooks, and `finish` — plus the request-scope
safety net that guarantees one `chat_turn` record per chat turn.
"""

import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.turnlog import begin_turn, current_turn


def make_app() -> object:
    return create_app(Settings(_env_file=None))


def test_begin_turn_inside_request_activates_the_request_scope_turn(
    json_lines,
) -> None:
    app = make_app()

    @app.get("/api/_fake_chat")
    async def fake_chat() -> dict:
        turn = begin_turn(session_id="session-1")
        in_endpoint = current_turn()
        assert in_endpoint is turn
        return {"turn_id": turn.turn_id}

    client = TestClient(app)
    response = client.get("/api/_fake_chat")

    assert response.status_code == 200
    turn_id = response.json()["turn_id"]
    chat_turns = json_lines.of_event("chat_turn")
    assert len(chat_turns) == 1
    assert chat_turns[0]["turn_id"] == turn_id
    assert chat_turns[0]["session_id"] == "session-1"
    access = json_lines.of_event("http_request")[0]
    assert access["turn_id"] == turn_id  # access record correlates with the turn


def test_turn_record_carries_tool_hops_latencies_tokens_and_validation(
    json_lines,
) -> None:
    app = make_app()

    @app.get("/api/_fake_chat")
    async def fake_chat() -> dict:
        turn = begin_turn(session_id="session-1")
        turn.record_tool_hop("search_products", latency_ms=12.3)
        turn.record_tool_hop("add_to_cart", latency_ms=4.1)
        turn.record_token_usage(prompt_tokens=120, completion_tokens=80)
        turn.record_validation(outcome="passed")
        turn.finish(status="ok")
        return {}

    client = TestClient(app)
    client.get("/api/_fake_chat")

    turn_record = json_lines.of_event("chat_turn")[0]
    assert turn_record["status"] == "ok"
    hops = turn_record["tool_hops"]
    assert [h["tool"] for h in hops] == ["search_products", "add_to_cart"]
    assert hops[0]["latency_ms"] == 12.3
    assert hops[0]["ok"] is True
    assert turn_record["token_usage"] == {
        "prompt_tokens": 120,
        "completion_tokens": 80,
    }
    assert turn_record["validation"] == {"outcome": "passed"}
    assert isinstance(turn_record["duration_ms"], (int, float))
    assert turn_record["duration_ms"] >= 0
    # still one machine-parseable JSON line per record
    assert len(json_lines.lines) == 2  # chat_turn + http_request


def test_unfinished_turn_is_finalized_by_the_request_scope(json_lines) -> None:
    app = make_app()

    @app.get("/api/_fake_chat")
    async def fake_chat() -> dict:
        turn = begin_turn(session_id="session-1")
        turn.record_tool_hop("search_products", latency_ms=9.9)
        return {}  # no finish() — middleware must finalize

    client = TestClient(app)
    client.get("/api/_fake_chat")

    turn_records = json_lines.of_event("chat_turn")
    assert len(turn_records) == 1
    assert turn_records[0]["status"] == "incomplete"
    assert turn_records[0]["tool_hops"][0]["tool"] == "search_products"


def test_errored_turn_is_finalized_with_error_status(json_lines) -> None:
    app = make_app()

    @app.get("/api/_fake_chat")
    async def fake_chat() -> dict:
        turn = begin_turn(session_id="session-1")
        raise RuntimeError("llm blew up")

    client = TestClient(app, raise_server_exceptions=False)
    client.get("/api/_fake_chat")

    turn_records = json_lines.of_event("chat_turn")
    assert len(turn_records) == 1
    assert turn_records[0]["status"] == "error"
    assert turn_records[0]["error"] == "llm blew up"
    assert turn_records[0]["error_type"] == "RuntimeError"


def test_begin_turn_outside_a_request_works_standalone(json_lines) -> None:
    """Background tasks (e.g. profile extraction) can run their own turn."""

    turn = begin_turn(session_id="session-2", context={"task": "profile"})
    turn.record_tool_hop("extract_profile", latency_ms=250.0)
    turn.finish(status="ok")

    turn_record = json_lines.of_event("chat_turn")[0]
    assert turn_record["session_id"] == "session-2"
    assert turn_record["context"] == {"task": "profile"}
    assert turn_record["status"] == "ok"
    assert turn_record["tool_hops"][0]["latency_ms"] == 250.0


def test_finish_is_idempotent(json_lines) -> None:
    turn = begin_turn(session_id="session-3")
    turn.finish(status="ok")
    turn.finish(status="error")  # second call must not emit another record

    assert len(json_lines.of_event("chat_turn")) == 1
    assert json_lines.of_event("chat_turn")[0]["status"] == "ok"


def test_plain_request_does_not_emit_a_chat_turn(json_lines) -> None:
    app = make_app()
    client = TestClient(app)

    client.get("/api/health")

    assert json_lines.of_event("chat_turn") == []
