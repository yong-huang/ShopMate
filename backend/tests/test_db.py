"""SQLite persistence layer (System Design 3.8: users/sessions/messages).

Seam: ``app.db`` — the engine/sessionmaker factory, the three v1 tables, and
the store operations the chat endpoint performs: get-or-create the anonymous
user behind the ``shopmate_sid`` cookie, get-or-create its sessions, append
messages (assistant incl. tool calls), and set session language.
"""

import uuid

import pytest
from sqlalchemy import select

from app.db import (
    Message,
    SessionRow,
    User,
    create_engine_and_sessionmaker,
    get_or_create_session,
    get_or_create_user,
    init_db,
    record_message,
    set_session_language,
)


@pytest.fixture
async def db(tmp_path):
    """An initialized engine + sessionmaker over a throwaway SQLite file."""
    url = f"sqlite+aiosqlite:///{tmp_path}/shopmate-test.db"
    engine, sessionmaker = create_engine_and_sessionmaker(url)
    await init_db(engine)
    return engine, sessionmaker


async def test_get_or_create_user_is_idempotent_per_cookie_id(db):
    _engine, sessionmaker = db
    user_id = uuid.uuid4().hex
    async with sessionmaker() as session:
        first = await get_or_create_user(session, user_id)
        await session.commit()
        second = await get_or_create_user(session, user_id)
        assert first.id == user_id == second.id

    async with sessionmaker() as session:
        users = (await session.execute(select(User))).scalars().all()
        assert len(users) == 1
        assert users[0].created_at is not None


async def test_session_row_belongs_to_user_with_defaults(db):
    _engine, sessionmaker = db
    user_id = uuid.uuid4().hex
    session_id = uuid.uuid4().hex
    async with sessionmaker() as session:
        await get_or_create_user(session, user_id)
        row = await get_or_create_session(session, session_id, user_id)
        await session.commit()
        assert row.id == session_id
        assert row.user_id == user_id
        assert row.started_at is not None
        assert row.ended_at is None
        assert row.lang is None

    # Re-fetching the same session id must not duplicate rows.
    async with sessionmaker() as session:
        rows = (await session.execute(select(SessionRow))).scalars().all()
        assert len(rows) == 1


async def test_record_message_persists_role_content_and_tool_calls(db):
    _engine, sessionmaker = db
    user_id = uuid.uuid4().hex
    session_id = uuid.uuid4().hex
    async with sessionmaker() as session:
        await get_or_create_user(session, user_id)
        await get_or_create_session(session, session_id, user_id)
        await record_message(
            session, session_id=session_id, role="user", content="trail shoes?"
        )
        await record_message(
            session,
            session_id=session_id,
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_cart", "arguments": "{}"},
                }
            ],
        )
        await session.commit()

    async with sessionmaker() as session:
        rows = (
            (await session.execute(select(Message).order_by(Message.id)))
            .scalars()
            .all()
        )
        assert [row.role for row in rows] == ["user", "assistant"]
        assert rows[0].content == "trail shoes?"
        assert rows[0].tool_calls is None
        assert rows[1].tool_calls[0]["function"]["name"] == "get_cart"
        assert all(row.created_at is not None for row in rows)
        assert all(row.session_id == session_id for row in rows)


async def test_set_session_language_updates_lang_column(db):
    _engine, sessionmaker = db
    user_id = uuid.uuid4().hex
    session_id = uuid.uuid4().hex
    async with sessionmaker() as session:
        await get_or_create_user(session, user_id)
        await get_or_create_session(session, session_id, user_id)
        await set_session_language(session, session_id, "zh")
        await session.commit()

    async with sessionmaker() as session:
        row = await session.get(SessionRow, session_id)
        assert row.lang == "zh"
