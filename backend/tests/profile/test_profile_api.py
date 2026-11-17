"""Profile control + interaction capture API (System Design 3.6 / 3.7).

Seam: the HTTP surface — ``GET`` / ``DELETE`` ``/api/profile`` and the
lightweight ``POST /api/products/{id}/view`` interaction hook the product
page calls. The LLM is not involved here; the store is the throwaway SQLite
file behind the real app.
"""

import uuid

import httpx
import pytest

from app.config import Settings
from app.db import get_profile
from app.main import create_app


def make_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{tmp_path}/shopmate.db",
        checkpoint_db_url=f"sqlite+aiosqlite:///{tmp_path}/checkpoints.db",
    )


@pytest.fixture
async def client(tmp_path):
    """Lifespan-open ASGI httpx client (fresh throwaway DB per test)."""
    app = create_app(settings=make_settings(tmp_path), chat_client=None)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            yield http, app


async def test_get_profile_is_empty_defaults_for_new_shopper(client):
    http, _app = client
    response = await http.get("/api/profile")
    assert response.status_code == 200
    body = response.json()
    assert body["interests"] == []
    assert body["price_band"] is None
    assert body["sizes"] == {}
    assert body["likes"] == []
    assert body["dislikes"] == []
    assert body["notes"] == []
    assert body["interaction_log"] == []
    assert body["updated_at"] is None


async def test_view_ping_records_interaction_and_issues_identity_cookie(client):
    http, app = client
    products = app.state.catalog.list_products(limit=1)
    product_id = products[0].id

    response = await http.post(f"/api/products/{product_id}/view")
    assert response.status_code == 200
    assert response.headers["set-cookie"].startswith("shopmate_sid=")

    profile = (await http.get("/api/profile")).json()
    assert len(profile["interaction_log"]) == 1
    event = profile["interaction_log"][0]
    assert event["type"] == "view"
    assert event["product_id"] == product_id
    assert event["at"]


async def test_view_ping_unknown_product_is_404_code_shape(client):
    http, _app = client
    response = await http.post("/api/products/NOPE-999/view")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert response.headers.get("set-cookie") is not None  # identity still issued


async def test_delete_profile_clears_everything_including_interaction_log(client):
    http, _app = client
    await http.post("/api/products/P-1001/view")  # seed an interaction

    response = await http.delete("/api/profile")
    assert response.status_code == 200

    profile = (await http.get("/api/profile")).json()
    assert profile["interests"] == []
    assert profile["interaction_log"] == []
    assert profile["updated_at"] is None


async def test_cleared_profile_not_in_next_prompt(tmp_path):
    """After DELETE, the next chat turn's system prompt has no profile block."""
    from tests.agent.mocks import ScriptedChatClient
    from app.gateway.events import FinishEvent, TokenDeltaEvent

    fake = ScriptedChatClient(
        [
            [TokenDeltaEvent(text="Hi!"), FinishEvent(finish_reason="stop")],
            [TokenDeltaEvent(text="Again!"), FinishEvent(finish_reason="stop")],
            [TokenDeltaEvent(text="Third!"), FinishEvent(finish_reason="stop")],
        ]
    )
    app = create_app(settings=make_settings(tmp_path), chat_client=fake)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            sid = uuid.uuid4().hex
            session_id = uuid.uuid4().hex
            # Turn 1: chat as a brand-new shopper (issues cookie + empty profile).
            await http.post(
                "/api/chat",
                json={"session_id": session_id, "message": "hello"},
            )
            assert "Shopper profile" not in fake.wire_messages(0)[0]["content"]

            # Seed a profile for the cookie identity, then clear it.
            from app.db import ensure_profile
            cookie = http.cookies["shopmate_sid"]
            async with app.state.sessionmaker() as db:
                profile = await ensure_profile(db, cookie)
                profile.interests = ["mountain bikes"]
                await db.commit()

            await http.post(
                "/api/chat",
                json={"session_id": session_id, "message": "hello again"},
            )
            assert "mountain bikes" in fake.wire_messages(-1)[0]["content"]

            await http.delete("/api/profile")
            await http.post(
                "/api/chat",
                json={"session_id": session_id, "message": "third hello"},
            )
            system_prompt = fake.wire_messages(-1)[0]["content"]
            assert "mountain bikes" not in system_prompt
            assert "Shopper profile" not in system_prompt
