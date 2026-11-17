"""SQLite persistence layer (System Design 3.8, ADR-004).

Delivers the v1 chat tables — ``users`` (anonymous, cookie-linked),
``sessions``, and ``messages`` (logs + eval source) — plus the ``profiles``
table (T07) and the cart/order tables (T06): ``carts`` / ``cart_items`` /
``orders`` / ``order_items`` (order lines snapshot name + unit price at
purchase time, System Design 3.8).

The chat endpoint (T04) is the primary consumer of the chat tables: it maps
the ``shopmate_sid`` cookie to a ``users`` row, opens ``sessions`` per
conversation, and appends user/assistant messages (assistant messages
carrying their ``tool_calls`` JSON) through :func:`record_message`. The
profile pipeline (T07) reads/writes ``profiles`` via
:func:`get_profile` / :func:`ensure_profile` /
:func:`record_interaction` / :func:`clear_profile` — T06 reuses
:func:`record_interaction` for added/purchased events.
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


class Profile(Base):
    """Cross-session preference profile (System Design 3.8 ``profiles``).

    ``field_sources`` carries the §3.6 merge bookkeeping: per preference
    field, which session last wrote it ({"field": {"session_id", "at"}}).
    The last-write-wins logic itself lives in ``app.profile.merge``.
    """

    __tablename__ = "profiles"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    interests: Mapped[list[str]] = mapped_column(JSON, default=list)
    price_band: Mapped[str | None] = mapped_column(Text, default=None)
    sizes: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    likes: Mapped[list[str]] = mapped_column(JSON, default=list)
    dislikes: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    interaction_log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    field_sources: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime | None] = mapped_column(default=None)


class Cart(Base):
    """One shopper's cart (System Design 3.8 ``carts``; user_id is the PK)."""

    __tablename__ = "carts"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    updated_at: Mapped[datetime | None] = mapped_column(default=None)


class CartItem(Base):
    """One cart line (System Design 3.8 ``cart_items``; variant JSON)."""

    __tablename__ = "cart_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cart_id: Mapped[str] = mapped_column(ForeignKey("carts.user_id"))
    product_id: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(default=1)
    variant: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)


class Order(Base):
    """A demo order (System Design 3.8 ``orders``; status ``demo_paid``)."""

    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    total: Mapped[float] = mapped_column(default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    status: Mapped[str] = mapped_column(String(16), default="demo_paid")
    created_at: Mapped[datetime] = mapped_column(default=_now)


class OrderItem(Base):
    """One order line with the purchase-time snapshot (3.8 ``order_items``).

    ``name_snapshot`` / ``unit_price`` are frozen when the order is written —
    later catalog changes never rewrite order history.
    """

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[str] = mapped_column(String(64))
    name_snapshot: Mapped[str] = mapped_column(Text)
    unit_price: Mapped[float] = mapped_column(default=0.0)
    quantity: Mapped[int] = mapped_column(default=1)
    variant: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)


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


# --- Profile store (System Design 3.6 / 3.8) -------------------------------
# Callers commit; the helpers here only flush, mirroring record_message.

INTERACTION_LOG_MAX = 100


async def get_profile(db: AsyncSession, user_id: str) -> Profile | None:
    """The user's profile row, or ``None`` when no profile exists yet."""
    return await db.get(Profile, user_id)


async def ensure_profile(db: AsyncSession, user_id: str) -> Profile:
    """Return the user's profile row, creating the empty default on demand."""
    profile = await db.get(Profile, user_id)
    if profile is None:
        profile = Profile(user_id=user_id)
        db.add(profile)
        await db.flush()
    return profile


async def record_interaction(
    db: AsyncSession,
    user_id: str,
    *,
    type: str,
    product_id: str | None = None,
    session_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Append one interaction event to the profile (§3.6 capture stage).

    The interaction log is capped to the most recent ``INTERACTION_LOG_MAX``
    events. T06 (cart) calls this same seam with ``add_to_cart`` /
    ``purchase`` events. Returns the stored event dict (callers commit).
    """
    profile = await ensure_profile(db, user_id)
    event: dict[str, Any] = {
        "type": type,
        "at": _now().isoformat(),
        "product_id": product_id,
        "session_id": session_id,
    }
    event.update(extra)
    log = list(profile.interaction_log or [])
    log.append(event)
    profile.interaction_log = log[-INTERACTION_LOG_MAX:]
    return event


async def clear_profile(db: AsyncSession, user_id: str) -> bool:
    """Delete the profile row entirely — preferences *and* interaction log.

    Returns whether a row existed. §3.6 control stage: nothing is re-injected
    after a clear because the row itself is gone.
    """
    profile = await db.get(Profile, user_id)
    if profile is None:
        return False
    await db.delete(profile)
    await db.flush()
    return True
