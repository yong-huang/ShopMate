"""Session-end preference extraction (System Design 3.6, ADR-007).

Seams:
- ``app.profile.extract.extract_preferences`` — the LLM boundary: a fake
  ``ChatClient`` (gateway seam, the only mock here) streams canned text and
  the extractor must produce validated preferences or ``None``.
- ``app.profile.service.run_profile_extraction`` — the background job over
  the real SQLite store: transcript in, merged profile out; malformed
  extractions discarded with a structured log, profile untouched.
"""

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db import (
    SessionRow,
    User,
    create_engine_and_sessionmaker,
    get_profile,
    init_db,
    record_message,
)
from app.gateway.events import FinishEvent, TokenDeltaEvent
from app.profile.extract import extract_preferences, parse_json_object
from app.profile.service import run_profile_extraction
from tests.agent.mocks import ScriptedChatClient


@pytest.fixture
async def db(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/shopmate-test.db"
    engine, sessionmaker = create_engine_and_sessionmaker(url)
    await init_db(engine)
    return engine, sessionmaker


async def _seed_transcript(sessionmaker, *, n_user_turns: int = 2) -> str:
    user_id, session_id = uuid.uuid4().hex, uuid.uuid4().hex
    async with sessionmaker() as db:
        db.add(User(id=user_id))
        db.add(SessionRow(id=session_id, user_id=user_id))
        for i in range(n_user_turns):
            await record_message(
                db, session_id=session_id, role="user", content=f"message {i}"
            )
            await record_message(
                db,
                session_id=session_id,
                role="assistant",
                content=f"reply {i}",
            )
        await db.commit()
    return user_id, session_id


VALID_EXTRACTION = {
    "interests": ["trail running"],
    "price_band": "$50-120",
    "sizes": {"shoes": "US 10"},
    "likes": ["Nike"],
    "dislikes": [],
    "notes": ["asked about return policy"],
}


def _text_client(text: str) -> ScriptedChatClient:
    return ScriptedChatClient(
        [[TokenDeltaEvent(text=text), FinishEvent(finish_reason="stop")]]
    )


def test_parse_json_object_handles_plain_fenced_and_noisy_text():
    assert parse_json_object('{"interests": ["yoga"]}') == {"interests": ["yoga"]}
    assert parse_json_object('```json\n{"interests": ["yoga"]}\n```') == {
        "interests": ["yoga"]
    }
    assert parse_json_object(
        'Here you go: {"interests": ["yoga"]} hope that helps'
    ) == {"interests": ["yoga"]}
    assert parse_json_object("no json at all") is None


async def test_extract_preferences_parses_streamed_json():
    client = _text_client(json.dumps(VALID_EXTRACTION))
    prefs = await extract_preferences(client, "user: looking for trail shoes\n")
    assert prefs is not None
    assert prefs.interests == ["trail running"]
    assert prefs.price_band == "$50-120"


async def test_extract_preferences_discards_garbage_text():
    client = _text_client("I could not produce any JSON, sorry!")
    assert await extract_preferences(client, "user: hello\n") is None


async def test_extract_preferences_discards_schema_invalid_json():
    client = _text_client('{"interests": "trail running"}')  # string, not list
    assert await extract_preferences(client, "user: hello\n") is None


async def test_extract_preferences_swallows_client_errors():
    client = ScriptedChatClient([RuntimeError("provider down")])
    assert await extract_preferences(client, "user: hello\n") is None


async def test_extract_preferences_sends_transcript_without_tools():
    client = _text_client("{}")
    await extract_preferences(client, "user: hi\nassistant: hello\n")
    call = client.calls[-1]
    assert call.tools in (None, [])
    wire = [m.to_wire() for m in call.messages]
    assert wire[0]["role"] == "system"
    assert "interests" in wire[0]["content"]  # schema described in the prompt
    assert any("user: hi" in (m["content"] or "") for m in wire[1:])


async def test_run_profile_extraction_merges_valid_result(db):
    _engine, sessionmaker = db
    user_id, session_id = await _seed_transcript(sessionmaker)
    client = _text_client(json.dumps(VALID_EXTRACTION))

    outcome = await run_profile_extraction(sessionmaker, client, session_id)

    assert outcome["status"] == "merged"
    async with sessionmaker() as session:
        profile = await get_profile(session, user_id)
        assert profile is not None
        assert profile.interests == ["trail running"]
        assert profile.price_band == "$50-120"
        assert profile.sizes == {"shoes": "US 10"}
        assert profile.field_sources["interests"]["session_id"] == session_id


async def test_run_profile_extraction_discards_malformed_without_corrupting(
    db, json_lines
):
    # This test never builds the app, so configure_logging() has not run:
    # raise the shopmate logger above pytest's WARNING root default so the
    # structured profile_extraction records reach the capture handler.
    logging.getLogger("shopmate").setLevel(logging.INFO)
    _engine, sessionmaker = db
    user_id, session_id = await _seed_transcript(sessionmaker)
    # Seed an existing good profile first.
    async with sessionmaker() as session:
        await run_profile_extraction(
            sessionmaker, _text_client(json.dumps(VALID_EXTRACTION)), session_id
        )
    json_lines.lines.clear()

    broken = ScriptedChatClient(
        [
            [
                TokenDeltaEvent(text="<think>hmm</think>not json"),
                FinishEvent(finish_reason="stop"),
            ]
        ]
    )
    outcome = await run_profile_extraction(sessionmaker, broken, session_id)

    assert outcome["status"] == "discarded"
    async with sessionmaker() as session:
        profile = await get_profile(session, user_id)
        assert profile.interests == ["trail running"]  # untouched
        assert profile.field_sources["interests"]["session_id"] == session_id
    discarded = json_lines.of_event("profile_extraction")
    assert any(entry["status"] == "discarded" for entry in discarded)


async def test_run_profile_extraction_skips_empty_transcript(db):
    _engine, sessionmaker = db
    user_id, session_id = uuid.uuid4().hex, uuid.uuid4().hex
    async with sessionmaker() as db_session:
        db_session.add(User(id=user_id))
        db_session.add(SessionRow(id=session_id, user_id=user_id))
        await db_session.commit()

    client = _text_client("{}")
    outcome = await run_profile_extraction(sessionmaker, client, session_id)
    assert outcome["status"] == "skipped"
    assert client.calls == []  # no LLM call for an empty transcript


async def test_run_profile_extraction_transcript_respects_session_end(db):
    """Messages persisted after ``ended_at`` belong to the next extraction."""
    _engine, sessionmaker = db
    user_id, session_id = uuid.uuid4().hex, uuid.uuid4().hex
    ended_at = datetime.now(UTC) - timedelta(minutes=5)
    async with sessionmaker() as db_session:
        db_session.add(User(id=user_id))
        db_session.add(
            SessionRow(id=session_id, user_id=user_id, ended_at=ended_at)
        )
        before = await record_message(
            db_session, session_id=session_id, role="user", content="in session"
        )
        before.created_at = ended_at - timedelta(minutes=1)
        after = await record_message(
            db_session, session_id=session_id, role="user", content="after end"
        )
        after.created_at = ended_at + timedelta(minutes=1)
        await db_session.commit()

    client = _text_client(json.dumps(VALID_EXTRACTION))
    await run_profile_extraction(sessionmaker, client, session_id)
    transcript_wire = [m.to_wire() for m in client.calls[-1].messages]
    flattened = json.dumps(transcript_wire)
    assert "in session" in flattened
    assert "after end" not in flattened


async def test_run_profile_extraction_on_unknown_session_is_safe(db):
    _engine, sessionmaker = db
    client = _text_client("{}")
    outcome = await run_profile_extraction(sessionmaker, client, "no-such-session")
    assert outcome["status"] == "skipped"
    assert client.calls == []
