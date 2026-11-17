"""Compact profile block for Maya's prompt (System Design 3.6).

The profile payload is serialized to a few ``- Field: values`` lines — one
per known field, plus a one-line summary of recent interactions — and hard-
capped (default 1200 chars ≈ 300 tokens) so injection stays cheap and the
prompt cannot balloon on a data-heavy profile. An empty profile renders as
an empty string, which ``compile_maya_prompt`` turns into no section at all.
"""

from typing import Any

from app.profile.merge import empty_profile

# ~300 tokens at ~4 chars/token; the cap is env-tunable via
# Settings.profile_injection_max_chars.
DEFAULT_MAX_CHARS = 1200

_INTERACTION_VERBS = {
    "view": "viewed",
    "viewed": "viewed",
    "add_to_cart": "added_to_cart",
    "added_to_cart": "added_to_cart",
    "purchase": "purchased",
    "purchased": "purchased",
}
_MAX_INTERACTION_EVENTS = 5


def _interaction_line(event: dict[str, Any]) -> str:
    raw_type = str(event.get("type", "view"))
    verb = _INTERACTION_VERBS.get(raw_type, raw_type)
    return f"{verb} {event.get('product_id', '?')}"


def _truncate_to_cap(lines: list[str], max_chars: int) -> str:
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def render_profile_block(
    profile: dict[str, Any] | None, max_chars: int = DEFAULT_MAX_CHARS
) -> str:
    """Serialize a profile payload into the ``PROFILE_BLOCK`` context text.

    Returns ``""`` for an empty/absent profile so the prompt template drops
    the section entirely.
    """
    data = profile or empty_profile()
    lines: list[str] = []
    if data.get("interests"):
        lines.append(f"- Interests: {', '.join(data['interests'])}")
    if data.get("price_band"):
        lines.append(f"- Price band: {data['price_band']}")
    sizes = data.get("sizes") or {}
    if sizes:
        rendered = ", ".join(f"{key}={value}" for key, value in sizes.items())
        lines.append(f"- Sizes: {rendered}")
    if data.get("likes"):
        lines.append(f"- Likes: {', '.join(data['likes'])}")
    if data.get("dislikes"):
        lines.append(f"- Dislikes: {', '.join(data['dislikes'])}")
    if data.get("notes"):
        lines.append(f"- Notes: {', '.join(data['notes'])}")
    events = [event for event in (data.get("interaction_log") or []) if event]
    if events:
        recent = events[-_MAX_INTERACTION_EVENTS:]
        lines.append(
            "Recent activity: " + ", ".join(_interaction_line(event) for event in recent)
        )
    if not lines:
        return ""
    return _truncate_to_cap(lines, max_chars)
