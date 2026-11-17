"""Profile pipeline orchestration (System Design 3.6, ADR-007).

``run_profile_extraction`` is the session-end background job: load the
session transcript, run the extraction LLM pass, merge into the profile
(per-field last-write-wins). ``schedule_extraction`` runs it off the request
path via ``asyncio.create_task`` — callers never await it on the hot path.

Session-end triggers (all funnel into ``end_session`` →
``schedule_extraction``):
- idle TTL: lazy check on session touch (chat endpoint)
- explicit new session: ``POST /api/sessions``
- browser unload ping: ``POST /api/sessions/{id}/end``
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.db import Message, SessionRow, ensure_profile, get_profile
from app.gateway.client import ChatClient
from app.logging_config import get_logger
from app.profile.extract import extract_preferences
from app.profile.merge import empty_profile, merge_extraction
from app.profile.schemas import ExtractedPreferences, PROFILE_FIELDS

logger = get_logger()

# Bounds for the extraction prompt (a long demo transcript stays cheap).
MAX_TRANSCRIPT_MESSAGES = 60

# Strong references keep scheduled tasks from being garbage-collected
# mid-flight (asyncio.create_task caveat); entries drop on completion.
_pending_tasks: set[asyncio.Task] = set()


async def load_session_transcript(
    db: AsyncSession, session_id: str, *, until: datetime | None = None
) -> str:
    """The user/assistant transcript of one session (oldest first).

    ``until`` (the session's ``ended_at``) bounds the transcript so messages
    persisted after an earlier extraction are not re-extracted.
    """
    query = (
        select(Message)
        .where(Message.session_id == session_id)
        .where(Message.role.in_(("user", "assistant")))
        .order_by(Message.created_at, Message.id)
        .limit(MAX_TRANSCRIPT_MESSAGES)
    )
    rows = (await db.execute(query)).scalars().all()
    if until is not None:
        until = _aware(until)
        rows = [row for row in rows if row.created_at and _aware(row.created_at) <= until]
    return "\n".join(f"{row.role}: {row.content}" for row in rows if row.content)


async def run_profile_extraction(
    sessionmaker: async_sessionmaker[AsyncSession],
    client: ChatClient,
    session_id: str,
) -> dict[str, Any]:
    """Extract + merge preferences for one ended session (background job).

    Returns an outcome dict (``merged`` / ``discarded`` / ``skipped``) for
    tests and logs; every path emits one structured ``profile_extraction``
    record so the pipeline is observable in the JSON log stream.
    """
    async with sessionmaker() as db:
        session_row = await db.get(SessionRow, session_id)
        if session_row is None:
            logger.info("profile_extraction", extra={"session_id": session_id, "status": "skipped", "reason": "unknown_session"})
            return {"status": "skipped", "reason": "unknown_session"}
        user_id = session_row.user_id
        transcript = await load_session_transcript(
            db, session_id, until=session_row.ended_at
        )
        if not transcript.strip():
            logger.info("profile_extraction", extra={"session_id": session_id, "user_id": user_id, "status": "skipped", "reason": "empty_transcript"})
            return {"status": "skipped", "reason": "empty_transcript"}

    extracted = await extract_preferences(client, transcript)
    if extracted is None:
        logger.info("profile_extraction", extra={"session_id": session_id, "user_id": user_id, "status": "discarded"})
        return {"status": "discarded"}

    merged_fields = await _merge_into_profile(sessionmaker, user_id, session_id, extracted)
    logger.info(
        "profile_extraction",
        extra={
            "session_id": session_id,
            "user_id": user_id,
            "status": "merged",
            "merged_fields": merged_fields,
        },
    )
    return {"status": "merged", "merged_fields": merged_fields}


async def _merge_into_profile(
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: str,
    session_id: str,
    extracted: ExtractedPreferences,
) -> list[str]:
    """Merge a validated extraction into the stored profile row (LWW)."""
    async with sessionmaker() as db:
        profile = await ensure_profile(db, user_id)
        payload = _profile_payload(profile)
        merged = merge_extraction(payload, extracted, session_id, datetime.now(UTC))
        _apply_payload(profile, merged)
        await db.commit()
        dumped = extracted.model_dump()
        return [field for field in PROFILE_FIELDS if dumped[field]]


def _profile_payload(profile) -> dict[str, Any]:
    """The dict shape shared by merge/render from a ``Profile`` row."""
    payload = empty_profile()
    payload.update(
        {
            "interests": list(profile.interests or []),
            "price_band": profile.price_band,
            "sizes": dict(profile.sizes or {}),
            "likes": list(profile.likes or []),
            "dislikes": list(profile.dislikes or []),
            "notes": list(profile.notes or []),
            "field_sources": dict(profile.field_sources or {}),
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
            "interaction_log": list(profile.interaction_log or []),
        }
    )
    return payload


def _apply_payload(profile, payload: dict[str, Any]) -> None:
    """Write a merged payload back onto the ``Profile`` row."""
    profile.interests = payload["interests"]
    profile.price_band = payload["price_band"]
    profile.sizes = payload["sizes"]
    profile.likes = payload["likes"]
    profile.dislikes = payload["dislikes"]
    profile.notes = payload["notes"]
    profile.field_sources = payload["field_sources"]
    if payload["updated_at"]:
        profile.updated_at = datetime.fromisoformat(payload["updated_at"])


def profile_payload(profile) -> dict[str, Any]:
    """Public payload projection (API + renderer) of a ``Profile`` row."""
    return _profile_payload(profile)


async def end_session(
    sessionmaker: async_sessionmaker[AsyncSession],
    session_id: str,
    *,
    ended_at: datetime | None = None,
) -> bool:
    """Close a session (``ended_at``); True when this call ended it.

    Idempotent: an already-ended session is left alone so its transcript
    boundary (and extraction) is not disturbed.
    """
    async with sessionmaker() as db:
        row = await db.get(SessionRow, session_id)
        if row is None or row.ended_at is not None:
            return False
        row.ended_at = ended_at or datetime.now(UTC)
        await db.commit()
        return True


def schedule_extraction(
    sessionmaker: async_sessionmaker[AsyncSession],
    client: ChatClient,
    session_id: str,
) -> asyncio.Task | None:
    """Kick off extraction off the request path (fire-and-forget task).

    Returns the task so tests can await it; callers on the request path
    ignore the return value.
    """
    task = asyncio.create_task(
        run_profile_extraction(sessionmaker, client, session_id),
        name=f"profile-extraction-{session_id or uuid.uuid4().hex}",
    )
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)
    return task


async def pending_extractions() -> None:
    """Await all in-flight extraction tasks (test/drain helper)."""
    if _pending_tasks:
        await asyncio.gather(*list(_pending_tasks), return_exceptions=False)


async def open_sessions_for_user(
    db: AsyncSession, user_id: str
) -> list[SessionRow]:
    """The user's sessions that have not ended yet (newest first)."""
    query = (
        select(SessionRow)
        .where(SessionRow.user_id == user_id, SessionRow.ended_at.is_(None))
        .order_by(SessionRow.started_at.desc())
    )
    return list((await db.execute(query)).scalars().all())


def _aware(moment: datetime) -> datetime:
    """SQLite round-trips datetimes naive; treat stored values as UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def end_session_if_idle(
    db: AsyncSession, session_id: str, *, ttl_min: int
) -> bool:
    """Lazy idle-TTL check on session touch (System Design 3.6).

    Ends the session when its last activity (newest message, else start) is
    at least ``ttl_min`` old; the caller then enqueues extraction. Returns
    whether this call ended the session. Runs inside the caller's transaction
    (flush only — the endpoint's commit persists it).
    """
    row = await db.get(SessionRow, session_id)
    if row is None or row.ended_at is not None:
        return False
    latest = await db.execute(
        select(Message.created_at)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    last_activity = latest.scalar_one_or_none() or row.started_at
    if last_activity is None:
        return False
    idle_for = datetime.now(UTC) - _aware(last_activity)
    if idle_for < timedelta(minutes=ttl_min):
        return False
    row.ended_at = datetime.now(UTC)
    return True


async def get_profile_payload(
    sessionmaker: async_sessionmaker[AsyncSession], user_id: str
) -> dict[str, Any]:
    """Read the profile as a payload dict (empty shape when absent)."""
    async with sessionmaker() as db:
        profile = await get_profile(db, user_id)
        return _profile_payload(profile) if profile else empty_profile()
