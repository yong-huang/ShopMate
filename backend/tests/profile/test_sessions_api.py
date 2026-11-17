"""Session-end triggers (System Design 3.6, ADR-007).

Seam: the HTTP surface again — ``POST /api/sessions`` (explicit new session),
``POST /api/sessions/{id}/end`` (browser unload ping), and the lazy idle-TTL
check on session touch (a chat turn after the idle window). The extraction
LLM is faked at the gateway boundary; everything else (SQLite, SSE, cookies)
is real. The off-path test proves extraction never blocks a response.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import Message, SessionRow, record_message
from app.gateway.events import FinishEvent, TokenDeltaEvent
from app.main import create_app
from app.profile.service import pending_extractions
from tests.agent.mocks import ScriptedChatClient

SESSION_COOKIE = "shopmate_sid"

VALID_EXTRACTION = {
    "interests": ["camping stoves"],
    "price_band": "$20-80",
    "sizes": {},
    "likes": [],
    "dislikes": [],
    "notes": [],
}


def make_settings(tmp_path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/shopmate.db",
        checkpoint_db_url=f"sqlite+aiosqlite:///{tmp_path}/checkpoints.db",
        **overrides,
    )


def extraction_client() -> ScriptedChatClient:
    """A scripted client returning one valid extraction JSON per call."""
    return ScriptedChatClient(
        [[
            TokenDeltaEvent(text=json.dumps(VALID_EXTRACTION)),
            FinishEvent(finish_reason="stop"),
        ]]
    )


def chat_client(*texts: str) -> ScriptedChatClient:
    """A scripted client with one text-only stream per chat turn."""
    return ScriptedChatClient(
        [[TokenDeltaEvent(text=text), FinishEvent(finish_reason="stop")] for text in texts]
    )


@pytest.fixture
async def open_app(tmp_path):
    """Build + lifespan-open an app; returns (app, http) per call.

    The caller picks the gateway fake (graph + extraction share it, as in
    production) and any settings overrides.
    """
    opened: list = []

    async def _open(chat_client=None, **settings_overrides):
        app = create_app(
            settings=make_settings(tmp_path, **settings_overrides),
            chat_client=chat_client,
        )
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()
        opened.append((app, lifespan))
        transport = httpx.ASGITransport(app=app)
        http = httpx.AsyncClient(transport=transport, base_url="http://test")
        return app, http

    yield _open
    for app, lifespan in opened:
        await lifespan.__aexit__(None, None, None)


async def seed_transcript(app, *, session_id: str, cookie: str) -> None:
    """Persist a two-turn transcript directly (fast, no LLM involved)."""
    async with app.state.sessionmaker() as db:
        db.add(SessionRow(id=session_id, user_id=cookie))
        await record_message(
            db, session_id=session_id, role="user", content="I need camping gear"
        )
        await record_message(
            db, session_id=session_id, role="assistant", content="Here are stoves."
        )
        await db.commit()


async def get_session(app, session_id: str) -> SessionRow:
    async with app.state.sessionmaker() as db:
        return await db.get(SessionRow, session_id)


async def test_new_session_issues_identity_and_creates_open_session(open_app):
    _app, http = await open_app(chat_client=extraction_client())
    response = await http.post("/api/sessions")
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"]
    assert body["user_id"]
    assert response.headers["set-cookie"].startswith("shopmate_sid=")
    row = await get_session(_app, body["session_id"])
    assert row is not None and row.ended_at is None


async def test_new_session_ends_prior_session_and_extracts(open_app):
    extraction = extraction_client()
    app, http = await open_app(chat_client=extraction)
    cookie = uuid.uuid4().hex
    prior = uuid.uuid4().hex
    await seed_transcript(app, session_id=prior, cookie=cookie)

    response = await http.post("/api/sessions", cookies={SESSION_COOKIE: cookie})
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == cookie
    assert body["session_id"] != prior
    await pending_extractions()  # background merge completes before asserting

    prior_row = await get_session(app, prior)
    assert prior_row.ended_at is not None

    # The prior session's extraction ran in the background and merged.
    profile = (await http.get("/api/profile", cookies={SESSION_COOKIE: cookie})).json()
    assert profile["interests"] == ["camping stoves"]
    assert profile["field_sources"]["interests"]["session_id"] == prior
    assert len(extraction.calls) == 1


async def test_end_ping_is_idempotent_and_extracts_once(open_app):
    extraction = extraction_client()
    app, http = await open_app(chat_client=extraction)
    cookie = uuid.uuid4().hex
    session_id = uuid.uuid4().hex
    await seed_transcript(app, session_id=session_id, cookie=cookie)

    first = await http.post(
        f"/api/sessions/{session_id}/end", cookies={SESSION_COOKIE: cookie}
    )
    assert first.status_code == 200
    await pending_extractions()
    assert (await get_session(app, session_id)).ended_at is not None

    second = await http.post(
        f"/api/sessions/{session_id}/end", cookies={SESSION_COOKIE: cookie}
    )
    assert second.status_code == 200
    assert len(extraction.calls) == 1  # no second extraction


async def test_end_ping_unknown_session_is_404(open_app):
    _app, http = await open_app(chat_client=extraction_client())
    response = await http.post(f"/api/sessions/{uuid.uuid4().hex}/end")
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_idle_ttl_trigger_fires_on_session_touch(open_app, tmp_path):
    chat = chat_client("hi there", "hi again")
    extraction = extraction_client()
    app, http = await open_app(
        chat_client=chat, session_idle_ttl_min=30
    )
    app.state.extraction_client = extraction  # separate fakes for chat/extraction
    cookie = uuid.uuid4().hex
    session_id = uuid.uuid4().hex
    # Transcript whose last activity is 20 minutes old.
    async with app.state.sessionmaker() as db:
        db.add(SessionRow(id=session_id, user_id=cookie))
        stale = await record_message(
            db, session_id=session_id, role="user", content="old message"
        )
        stale.created_at = datetime.now(UTC) - timedelta(minutes=20)
        await db.commit()

    response = await http.post(
        "/api/chat",
        cookies={SESSION_COOKIE: cookie},
        json={"session_id": session_id, "message": "hello again"},
    )
    assert response.status_code == 200
    row = await get_session(app, session_id)
    assert row.ended_at is None  # 20 min idle < 30 min TTL
    assert len(extraction.calls) == 0

    # Now backdate past the TTL: the next touch must end + extract.
    async with app.state.sessionmaker() as db:
        row = await db.get(SessionRow, session_id)
        row.ended_at = None
        messages = (
            await db.execute(select(Message).where(Message.session_id == session_id))
        ).scalars().all()
        for message in messages:
            message.created_at = datetime.now(UTC) - timedelta(minutes=45)
        await db.commit()

    await http.post(
        "/api/chat",
        cookies={SESSION_COOKIE: cookie},
        json={"session_id": session_id, "message": "still there?"},
    )
    await pending_extractions()
    row = await get_session(app, session_id)
    assert row.ended_at is not None
    assert len(extraction.calls) == 1
    profile = (await http.get("/api/profile", cookies={SESSION_COOKIE: cookie})).json()
    assert profile["interests"] == ["camping stoves"]


async def test_extraction_runs_off_the_request_path(open_app):
    """The response arrives while extraction is still waiting on the LLM."""

    class GatedExtractionClient:
        """Gateway fake whose stream blocks until ``release`` is set."""

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls: list = []

        async def stream(self, messages, tools=None, temperature=0.3):
            self.calls.append(list(messages))
            self.started.set()
            await self.release.wait()
            yield TokenDeltaEvent(text=json.dumps(VALID_EXTRACTION))
            yield FinishEvent(finish_reason="stop")

    gated = GatedExtractionClient()
    app, http = await open_app(chat_client=gated)
    cookie = uuid.uuid4().hex
    session_id = uuid.uuid4().hex
    await seed_transcript(app, session_id=session_id, cookie=cookie)

    end_response = await http.post(
        f"/api/sessions/{session_id}/end", cookies={SESSION_COOKIE: cookie}
    )
    # The unload ping returned even though extraction cannot proceed yet.
    assert end_response.status_code == 200
    await gated.started.wait()
    assert (await get_session(app, session_id)).ended_at is not None

    gated.release.set()
    await pending_extractions()
    profile = (await http.get("/api/profile", cookies={SESSION_COOKIE: cookie})).json()
    assert profile["interests"] == ["camping stoves"]
