"""SQLite persistence layer (System Design 3.8, ADR-004).

Delivers the three v1 chat tables — ``users`` (anonymous, cookie-linked),
``sessions``, and ``messages`` (logs + eval source). Cart, order, and profile
tables belong to later tickets and are added to ``Base.metadata`` there.

The chat endpoint (T04) is the primary consumer: it maps the ``shopmate_sid``
cookie to a ``users`` row, opens ``sessions`` per conversation, and appends
user/assistant messages (assistant messages carrying their ``tool_calls``
JSON) through :func:`record_message`.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for all ShopMate tables."""


class User(Base):
    """Anonymous shopper identified by the ``shopmate_sid`` cookie value."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class SessionRow(Base):
    """One chat session (``sessions`` table; ``Session`` shadows SQLAlchemy)."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(default=None)
    lang: Mapped[str | None] = mapped_column(String(8), default=None)


class Message(Base):
    """One persisted conversation message (user / assistant / tool)."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str | None] = mapped_column(Text, default=None)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(default=_now)


def create_engine_and_sessionmaker(
    database_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Build the async engine and its session factory for ``database_url``."""
    engine = create_async_engine(database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """Create all tables that are registered so far (idempotent)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_or_create_user(db: AsyncSession, user_id: str) -> User:
    """Return the user for a cookie id, creating the row on first sight."""
    user = await db.get(User, user_id)
    if user is None:
        user = User(id=user_id)
        db.add(user)
        await db.flush()
    return user


async def get_or_create_session(db: AsyncSession, session_id: str, user_id: str) -> SessionRow:
    """Return the session row for ``session_id``, creating it when new."""
    row = await db.get(SessionRow, session_id)
    if row is None:
        row = SessionRow(id=session_id, user_id=user_id)
        db.add(row)
        await db.flush()
    return row


async def record_message(
    db: AsyncSession,
    *,
    session_id: str,
    role: str,
    content: str | None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> Message:
    """Append one message row and return it (callers commit)."""
    row = Message(session_id=session_id, role=role, content=content, tool_calls=tool_calls)
    db.add(row)
    await db.flush()
    return row


async def set_session_language(db: AsyncSession, session_id: str, lang: str) -> None:
    """Best-effort language stamp on the session (System Design 3.8 ``lang``)."""
    row = await db.get(SessionRow, session_id)
    if row is not None:
        row.lang = lang


async def session_message_count(db: AsyncSession, session_id: str) -> int:
    """Number of persisted messages for one session."""
    rows = await db.execute(select(Message).where(Message.session_id == session_id))
    return len(rows.scalars().all())
