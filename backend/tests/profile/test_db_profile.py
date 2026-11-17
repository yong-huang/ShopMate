"""Profile store operations in ``app.db`` (System Design 3.8).

Seam: the same ``app.db`` store functions the API and the extraction service
use — ``get_profile`` / ``ensure_profile`` / ``record_interaction`` /
``clear_profile`` — over a throwaway SQLite file.
"""

import uuid

import pytest
from sqlalchemy import select

from app.db import (
    Profile,
    clear_profile,
    create_engine_and_sessionmaker,
    ensure_profile,
    get_or_create_user,
    get_profile,
    init_db,
    record_interaction,
)


@pytest.fixture
async def db(tmp_path):
    """An initialized engine + sessionmaker over a throwaway SQLite file."""
    url = f"sqlite+aiosqlite:///{tmp_path}/shopmate-test.db"
    engine, sessionmaker = create_engine_and_sessionmaker(url)
    await init_db(engine)
    return engine, sessionmaker


async def _make_user(sessionmaker) -> str:
    user_id = uuid.uuid4().hex
    async with sessionmaker() as db:
        await get_or_create_user(db, user_id)
        await db.commit()
    return user_id


async def test_profile_absent_by_default_and_ensure_creates_row(db):
    _engine, sessionmaker = db
    user_id = await _make_user(sessionmaker)
    async with sessionmaker() as session:
        assert await get_profile(session, user_id) is None
        profile = await ensure_profile(session, user_id)
        await session.commit()
        assert profile.user_id == user_id
        assert profile.interests == []
        assert profile.interaction_log == []


async def test_record_interaction_appends_event_and_persists(db):
    _engine, sessionmaker = db
    user_id = await _make_user(sessionmaker)
    async with sessionmaker() as session:
        event = await record_interaction(
            session, user_id, type="view", product_id="P-1001", session_id="sess-1"
        )
        await session.commit()
        assert event["type"] == "view"
        assert event["product_id"] == "P-1001"
        assert event["session_id"] == "sess-1"
        assert event["at"]

    async with sessionmaker() as session:
        profile = await get_profile(session, user_id)
        assert [e["product_id"] for e in profile.interaction_log] == ["P-1001"]


async def test_record_interaction_is_capped_to_recent_events(db):
    _engine, sessionmaker = db
    user_id = await _make_user(sessionmaker)
    async with sessionmaker() as session:
        for i in range(130):
            await record_interaction(
                session, user_id, type="view", product_id=f"P-{i:04d}"
            )
        await session.commit()
    async with sessionmaker() as session:
        profile = await get_profile(session, user_id)
        log = profile.interaction_log
        assert len(log) == 100  # INTERACTION_LOG_MAX
        assert log[0]["product_id"] == "P-0030"  # oldest evicted
        assert log[-1]["product_id"] == "P-0129"


async def test_clear_profile_deletes_the_row_entirely(db):
    _engine, sessionmaker = db
    user_id = await _make_user(sessionmaker)
    async with sessionmaker() as session:
        await ensure_profile(session, user_id)
        await record_interaction(session, user_id, type="view", product_id="P-1")
        await session.commit()

    async with sessionmaker() as session:
        cleared = await clear_profile(session, user_id)
        await session.commit()
        assert cleared is True

    async with sessionmaker() as session:
        assert await get_profile(session, user_id) is None
        rows = (await session.execute(select(Profile))).scalars().all()
        assert rows == []  # interaction log died with the row


async def test_clear_profile_without_row_is_a_no_op(db):
    _engine, sessionmaker = db
    user_id = await _make_user(sessionmaker)
    async with sessionmaker() as session:
        assert await clear_profile(session, user_id) is False
