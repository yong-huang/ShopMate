"""Per-field last-write-wins profile merge (System Design 3.6, ADR-007).

A validated extraction is merged into the existing profile payload field by
field: a non-empty extracted value overwrites the stored one and records its
source session + timestamp; an empty/absent value writes nothing, so the
previous session's data (and its source stamp) survive untouched. There is
no conflict dialog in v1 — the newer session always wins per field.

The profile payload dict shape shared by the store, API, and renderer:

    {
      "interests": [...], "price_band": str | None, "sizes": {...},
      "likes": [...], "dislikes": [...], "notes": [...],
      "field_sources": {field: {"session_id": ..., "at": iso}},
      "updated_at": iso | None,
      "interaction_log": [event, ...],
    }
"""

from datetime import datetime
from typing import Any

from app.profile.schemas import ExtractedPreferences, PROFILE_FIELDS


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def empty_profile() -> dict[str, Any]:
    """The payload shape of a shopper with no learned profile yet."""
    return {
        "interests": [],
        "price_band": None,
        "sizes": {},
        "likes": [],
        "dislikes": [],
        "notes": [],
        "field_sources": {},
        "updated_at": None,
        "interaction_log": [],
    }


def merge_extraction(
    existing: dict[str, Any] | None,
    extracted: ExtractedPreferences,
    session_id: str,
    at: datetime,
) -> dict[str, Any]:
    """Merge one validated extraction into ``existing`` (per-field LWW).

    Returns a new payload dict; the input is not mutated. ``interaction_log``
    passes through untouched — the merge only arbitrates preference fields.
    """
    merged = dict(existing) if existing else empty_profile()
    merged.setdefault("field_sources", {})
    sources: dict[str, Any] = dict(merged["field_sources"])

    field_values: dict[str, Any] = {
        "interests": extracted.interests,
        "price_band": extracted.price_band,
        "sizes": extracted.sizes,
        "likes": extracted.likes,
        "dislikes": extracted.dislikes,
        "notes": extracted.notes,
    }
    stamp = {"session_id": session_id, "at": _iso(at)}
    wrote = False
    for field in PROFILE_FIELDS:
        value = field_values[field]
        if not value:  # empty list / {} / None → no write, keep old value
            continue
        merged[field] = value
        sources[field] = dict(stamp)
        wrote = True

    merged["field_sources"] = sources
    if wrote:
        merged["updated_at"] = stamp["at"]
    elif not merged.get("updated_at"):
        merged["updated_at"] = None
    return merged
