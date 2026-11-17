"""Session-end preference extraction LLM pass (System Design 3.6, ADR-007).

One chat-completions call over the session transcript with a JSON-schema
prompt. The reply is parsed defensively (bare JSON, fenced code block, or
JSON embedded in chatter), validated by
``app.profile.schemas.validate_extraction``, and either returned or rejected
as ``None`` — a malformed extraction is the caller's signal to discard and
log, never to merge.

Runs only in background contexts (never on the request path); the same
gateway ``ChatClient`` facade serves GLM and Ollama alike.
"""

import json
from typing import Any

from app.gateway.client import ChatClient
from app.gateway.messages import ChatMessage
from app.logging_config import get_logger
from app.profile.schemas import ExtractedPreferences, PROFILE_FIELDS, validate_extraction

logger = get_logger()

_EXTRACTION_SYSTEM_PROMPT = """You extract shopper preferences from a chat \
transcript for a shopping assistant's cross-session profile.

Return ONLY a JSON object with exactly these fields (no prose, no markdown \
fences, no commentary):
{
  "interests": ["..."],          // product interests/topics, inferred
  "price_band": "...",           // budget band like "$50-120", or null
  "sizes": {"category": "size"}, // e.g. {"shoes": "US 10"}, or {}
  "likes": ["..."],              // brands/features the shopper responded to
  "dislikes": ["..."],           // brands/features the shopper rejected
  "notes": ["..."]               // short durable facts worth remembering
}

Rules:
- Every list element must be a short string (max ~200 chars).
- Omit nothing silently: use [] / {} / null for unknown fields.
- If the transcript shows no preference signal at all, return all fields \
empty.
- Output raw JSON only."""


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model reply.

    Tries the whole text, then a fenced code block, then the widest
    ``{...}`` span — small models like to chat around the payload.
    """
    text = text.strip()
    for candidate in (text, *_fenced_blocks(text)):
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _fenced_blocks(text: str) -> list[str]:
    blocks = []
    fence = "```"
    if fence in text:
        for chunk in text.split(fence)[1::2]:
            body = chunk.split("\n", 1)[1] if "\n" in chunk else chunk
            blocks.append(body.strip())
    return blocks


async def extract_preferences(
    client: ChatClient, transcript: str
) -> ExtractedPreferences | None:
    """Run the extraction LLM over ``transcript``; None on any failure.

    Any provider error is swallowed into ``None`` — the extraction is
    best-effort by design (ADR-007) and must never take a request down.
    """
    messages = [
        ChatMessage(role="system", content=_EXTRACTION_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=f"Session transcript:\n\n{transcript}\n\nReturn the JSON object only.",
        ),
    ]
    try:
        parts: list[str] = []
        async for event in client.stream(messages, tools=None, temperature=0.0):
            if event.type == "token_delta":
                parts.append(event.text)
        reply = "".join(parts)
    except Exception:  # noqa: BLE001 — best-effort extraction, logged upstream
        logger.warning("profile_extraction_provider_error")
        return None
    payload = parse_json_object(reply)
    if payload is None:
        logger.warning(
            "profile_extraction_unparseable", extra={"reply_snippet": reply[:200]}
        )
        return None
    prefs = validate_extraction(payload)
    if prefs is None:
        logger.warning(
            "profile_extraction_schema_invalid",
            extra={"reply_snippet": reply[:200], "expected_fields": list(PROFILE_FIELDS)},
        )
        return None
    return prefs
