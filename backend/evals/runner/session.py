"""The per-scenario HTTP surface: SSE chat turns + storefront REST calls.

``ChatSession`` mirrors a browser: one cookie-jarred ``httpx.AsyncClient``
over ASGI transport against the live app, a fixed anonymous ``shopmate_sid``
(pre-set so the user_id is known before the first request — profile/cart
seeding needs it), and a fresh UUID session id per scenario.

``send_turn`` POSTs one user message and folds the §3.7 SSE stream
(``session``/``token``/``tool_call``/``cart_update``/``product_card``/
``comparison``/``done``|``error``) into an observation dict. Cart, profile,
and session endpoints ride the same client so identity is shared.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
from fastapi import FastAPI

SESSION_COOKIE = "shopmate_sid"


def parse_sse(raw: str) -> list[dict[str, Any]]:
    """Parse an SSE body into [{event, data}, ...] in wire order.

    Same frame grammar as production (app.api.chat._sse) and the frontend
    parser (frontend/lib/chat-sse.ts): ``event:`` + one JSON ``data:`` line.
    """
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        if not block:
            continue
        name: str | None = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: "):])
        if name is None:
            continue
        data = json.loads("\n".join(data_lines)) if data_lines else None
        events.append({"event": name, "data": data})
    return events


class ChatSession:
    """One shopper's session against a live app (chat + cart + profile)."""

    def __init__(self, app: FastAPI, user_id: str | None = None) -> None:
        self.app = app
        # Pre-set cookie: the user_id is deterministic before the first
        # request (needed to seed the profile row).
        self.user_id = user_id or uuid.uuid4().hex
        self.session_id = uuid.uuid4().hex
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://eval.test",
            cookies={SESSION_COOKIE: self.user_id},
            timeout=httpx.Timeout(300.0),
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    # --- chat ---------------------------------------------------------------

    async def send_turn(self, text: str) -> dict[str, Any]:
        """One chat turn; folds the SSE stream into an observation dict.

        Keys: ``reply`` (concatenated token text), ``cards`` (rendered
        product_card payloads), ``comparison``, ``cart_update``,
        ``tool_calls`` (SSE progress summaries: requested name + args),
        ``error`` (SSE error message or HTTP failure), ``done`` (bool).
        """
        response = await self.http.post(
            "/api/chat", json={"session_id": self.session_id, "message": text}
        )
        if response.status_code != 200:
            return {
                "reply": "",
                "cards": [],
                "comparison": None,
                "cart_update": None,
                "tool_calls": [],
                "error": f"HTTP {response.status_code}: {response.text[:300]}",
                "done": False,
            }
        observation: dict[str, Any] = {
            "reply": "",
            "cards": [],
            "comparison": None,
            "cart_update": None,
            "tool_calls": [],
            "error": None,
            "done": False,
        }
        parts: list[str] = []
        for event in parse_sse(response.text):
            name, data = event["event"], event["data"]
            if name == "token":
                parts.append(data["text"])
            elif name == "tool_call":
                observation["tool_calls"].append(
                    {"name": data["name"], "args_summary": data.get("args_summary", "")}
                )
            elif name == "product_card":
                observation["cards"].append(data)
            elif name == "comparison":
                observation["comparison"] = data
            elif name == "cart_update":
                observation["cart_update"] = data
            elif name == "done":
                observation["done"] = True
            elif name == "error":
                observation["error"] = data.get("message", "unknown error")
        observation["reply"] = "".join(parts)
        return observation

    # --- storefront REST (cart API assertions between turns, §2.3) -----------

    async def cart_summary(self) -> dict[str, Any]:
        """GET /api/cart — the same payload cart assertions compare against."""
        response = await self.http.get("/api/cart")
        response.raise_for_status()
        return response.json()

    async def add_cart_item(
        self, product_id: str, quantity: int = 1, variant: dict | None = None
    ) -> dict[str, Any]:
        """POST /api/cart/items — scenario ``seed_cart`` setup."""
        response = await self.http.post(
            "/api/cart/items",
            json={"product_id": product_id, "quantity": quantity, "variant": variant},
        )
        response.raise_for_status()
        return response.json()

    # --- profile / session lifecycle ------------------------------------------

    async def clear_profile(self) -> None:
        """DELETE /api/profile — the preferences-panel clear (AC6.3)."""
        response = await self.http.delete("/api/profile")
        response.raise_for_status()

    async def end_session(self) -> dict[str, Any]:
        """POST /api/sessions/{id}/end — closes the session + enqueues extraction."""
        response = await self.http.post(f"/api/sessions/{self.session_id}/end")
        response.raise_for_status()
        return response.json()
