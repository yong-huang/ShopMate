"""POST /api/chat — the SSE chat endpoint (System Design 3.7).

Seam: the HTTP boundary. Tests drive the real FastAPI app (composition root)
with the ChatClient faked at the gateway boundary and both SQLite databases
pointed at throwaway files, then assert the exact SSE event contract
(``session`` / ``token`` / ``tool_call`` / ``done`` / ``error``), the
``shopmate_sid`` cookie behavior, message persistence, and the turn log.
"""

import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import Message, SessionRow
from app.gateway.errors import ProviderHTTPError
from app.gateway.events import FinishEvent, TokenDeltaEvent, ToolCallEvent
from app.gateway.tools import ToolCall

from .agent.mocks import ScriptedChatClient

SESSION_COOKIE = "shopmate_sid"


def make_settings(tmp_path) -> Settings:
    # _env_file=None keeps tests hermetic: a developer's backend/.env (e.g.
    # LLM_PROVIDER=ollama for a live run) must not leak into test settings.
    # embedding_provider="none" also keeps the retrieval index keyword-only,
    # so no test ever contacts a live embedding backend.
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/shopmate.db",
        checkpoint_db_url=f"sqlite+aiosqlite:///{tmp_path}/checkpoints.db",
        embedding_provider="none",
    )


async def open_app(tmp_path, chat_client=None, settings=None):
    """Build the app with its lifespan (DB init, graph) running."""
    from app.main import create_app

    app = create_app(settings=settings or make_settings(tmp_path), chat_client=chat_client)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def app_factory(tmp_path):
    """Callable fixture returning a lifespan-open app (caller picks the fake)."""
    opened = []

    async def _open(chat_client=None, settings=None):
        agen = open_app(tmp_path, chat_client, settings)
        app = await agen.__anext__()
        opened.append(agen)
        return app

    yield _open
    for agen in opened:
        try:
            await agen.__anext__()
        except StopAsyncIteration:
            pass


async def stream_chat(app, payload, client=None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=client) as http:
        return await http.post("/api/chat", json=payload)


def parse_sse(raw: str) -> list[dict]:
    """Parse an SSE body into [{event, data}, ...] in wire order."""
    events: list[dict] = []
    for block in raw.strip().split("\n\n"):
        if not block:
            continue
        name = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: "):])
        if name is not None:
            data = json.loads("\n".join(data_lines)) if data_lines else None
            events.append({"event": name, "data": data})
    return events


async def test_stream_emits_session_token_tool_call_done_in_order(app_factory):
    fake = ScriptedChatClient(
        [
            [
                TokenDeltaEvent(text="Let me look that up."),
                ToolCallEvent(call=ToolCall(id="call_1", name="get_store_info", arguments={})),
                FinishEvent(finish_reason="tool_calls"),
            ],
            [
                TokenDeltaEvent(text="ShopMate sells four categories."),
                FinishEvent(finish_reason="stop"),
            ],
        ]
    )
    app = await app_factory(fake)
    session_id = uuid.uuid4().hex

    response = await stream_chat(
        app, {"session_id": session_id, "message": "what do you sell?"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    assert [event["event"] for event in events] == [
        "session",
        "token",
        "tool_call",
        "token",
        "done",
    ]
    assert events[0]["data"]["session_id"] == session_id
    assert events[0]["data"]["user_id"]
    assert events[1]["data"] == {"text": "Let me look that up."}
    assert events[2]["data"] == {"name": "get_store_info", "args_summary": "{}"}
    assert events[3]["data"] == {"text": "ShopMate sells four categories."}
    assert isinstance(events[4]["data"]["message_id"], int)


async def test_session_cookie_issued_on_first_request_then_reused(app_factory):
    fake_1 = ScriptedChatClient([[TokenDeltaEvent(text="Hello!"), FinishEvent(finish_reason="stop")]])
    fake_2 = ScriptedChatClient([[TokenDeltaEvent(text="Hi again!"), FinishEvent(finish_reason="stop")]])
    app_1 = await app_factory(fake_1)
    app_2 = await app_factory(fake_2)
    session_id = uuid.uuid4().hex

    first = await stream_chat(app_1, {"session_id": session_id, "message": "hi"})
    set_cookie = first.headers["set-cookie"]
    assert f"{SESSION_COOKIE}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Path=/" in set_cookie

    # A client that stores the cookie (normal browser behavior) keeps the
    # same anonymous user across turns.
    sid = set_cookie.split(f"{SESSION_COOKIE}=")[1].split(";")[0]
    second = await stream_chat(
        app_2, {"session_id": session_id, "message": "hello again"}, client={SESSION_COOKIE: sid}
    )
    user_first = parse_sse(first.text)[0]["data"]["user_id"]
    user_second = parse_sse(second.text)[0]["data"]["user_id"]
    assert user_first == user_second == sid


async def test_new_cookie_means_new_anonymous_user(app_factory):
    fake = ScriptedChatClient(
        [
            [TokenDeltaEvent(text="Hi!"), FinishEvent(finish_reason="stop")],
            [TokenDeltaEvent(text="Hi!"), FinishEvent(finish_reason="stop")],
        ]
    )
    app = await app_factory(fake)
    first = await stream_chat(app, {"session_id": uuid.uuid4().hex, "message": "hi"})
    second = await stream_chat(app, {"session_id": uuid.uuid4().hex, "message": "hi"})
    user_a = parse_sse(first.text)[0]["data"]["user_id"]
    user_b = parse_sse(second.text)[0]["data"]["user_id"]
    assert user_a != user_b


async def test_messages_persisted_including_tool_calls(app_factory, tmp_path):
    fake = ScriptedChatClient(
        [
            [
                TokenDeltaEvent(text="Looking it up."),
                ToolCallEvent(call=ToolCall(id="call_9", name="get_store_info", arguments={})),
                FinishEvent(finish_reason="tool_calls"),
            ],
            [
                TokenDeltaEvent(text="Here is what the store sells."),
                FinishEvent(finish_reason="stop"),
            ],
        ]
    )
    app = await app_factory(fake)
    session_id = uuid.uuid4().hex

    await stream_chat(app, {"session_id": session_id, "message": "what do you sell?"})

    async with app.state.sessionmaker() as db:
        rows = (
            (await db.execute(select(Message).where(Message.session_id == session_id)))
            .scalars()
            .all()
        )
    assert [row.role for row in rows] == ["user", "assistant", "tool", "assistant"]
    assert rows[0].content == "what do you sell?"
    assert rows[1].tool_calls[0]["function"]["name"] == "get_store_info"
    assert "ShopMate" in rows[2].content
    assert rows[3].content == "Here is what the store sells."

    async with app.state.sessionmaker() as db:
        session_row = await db.get(SessionRow, session_id)
        assert session_row is not None
        assert session_row.lang == "en"


async def test_conversation_second_turn_replays_history_via_checkpointer(app_factory):
    fake = ScriptedChatClient(
        [
            [TokenDeltaEvent(text="First answer."), FinishEvent(finish_reason="stop")],
            [TokenDeltaEvent(text="Second answer."), FinishEvent(finish_reason="stop")],
        ]
    )
    app = await app_factory(fake)
    session_id = uuid.uuid4().hex

    await stream_chat(app, {"session_id": session_id, "message": "first question"})
    await stream_chat(app, {"session_id": session_id, "message": "second question"})

    wire = fake.wire_messages(1)
    assert {"role": "user", "content": "first question"} in wire
    assert {"role": "assistant", "content": "First answer."} in wire


async def test_provider_failure_emits_model_error_event(app_factory):
    failure = ProviderHTTPError(provider="glm", status_code=503, body_snippet="boom")
    fake = ScriptedChatClient([[failure]])
    app = await app_factory(fake)

    response = await stream_chat(app, {"session_id": uuid.uuid4().hex, "message": "hi"})

    assert response.status_code == 200
    events = parse_sse(response.text)
    names = [event["event"] for event in events]
    assert names[-1] == "error"
    error = events[-1]["data"]
    assert error["code"] == "model"
    assert "503" in error["message"]


async def test_unconfigured_provider_emits_error_event(tmp_path):
    # Default settings (glm) without LLM_API_KEY: the gateway cannot be
    # built; the endpoint degrades honestly instead of crashing.
    from app.main import create_app

    settings = make_settings(tmp_path)
    app = create_app(settings=settings, chat_client=None)
    async with app.router.lifespan_context(app):
        response = await stream_chat(
            app, {"session_id": uuid.uuid4().hex, "message": "hi"}
        )
    events = parse_sse(response.text)
    assert events[-1]["event"] == "error"
    assert events[-1]["data"]["code"] == "model"


async def test_invalid_body_is_json_validation_error(app_factory):
    app = await app_factory(ScriptedChatClient([]))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.post("/api/chat", json={"session_id": "not-a-uuid"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"


async def test_chat_turn_log_records_hops_usage_and_ok_status(app_factory, json_lines):
    fake = ScriptedChatClient(
        [
            [
                TokenDeltaEvent(text="Looking."),
                ToolCallEvent(call=ToolCall(id="call_1", name="get_store_info", arguments={})),
                FinishEvent(finish_reason="tool_calls", usage={"prompt_tokens": 90, "completion_tokens": 12}),
            ],
            [
                TokenDeltaEvent(text="Done!"),
                FinishEvent(finish_reason="stop"),
            ],
        ]
    )
    app = await app_factory(fake)
    session_id = uuid.uuid4().hex

    await stream_chat(app, {"session_id": session_id, "message": "hi"})

    turns = json_lines.of_event("chat_turn")
    assert len(turns) == 1
    record = turns[0]
    assert record["session_id"] == session_id
    assert record["status"] == "ok"
    assert [hop["tool"] for hop in record["tool_hops"]] == ["get_store_info"]
    assert record["token_usage"]["prompt_tokens"] == 90
    assert record["token_usage"]["completion_tokens"] == 12
