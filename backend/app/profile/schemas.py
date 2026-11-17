"""Schema gate for extracted preferences (System Design 3.6, ADR-007).

The extraction LLM returns free-text JSON; everything it produced passes
through :func:`validate_extraction` before anything touches the profile.
Validation failure means the *whole* extraction is discarded (the caller
logs and moves on) — the profile is never partially corrupted by a
half-valid payload.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

# Normalization caps: an extraction is a summary, not a data dump. Oversized
# or over-long values are truncated so neither the stored profile nor the
# injected context block can grow unbounded (System Design 3.6 injection cap).
MAX_LIST_ITEMS = 20
MAX_ITEM_CHARS = 200

PROFILE_FIELDS = ("interests", "price_band", "sizes", "likes", "dislikes", "notes")


class ExtractedPreferences(BaseModel):
    """One schema-validated extraction result (§3.6 extraction shape)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    interests: list[str] = []
    price_band: str | None = None
    sizes: dict[str, str] = {}
    likes: list[str] = []
    dislikes: list[str] = []
    notes: list[str] = []


def _clean_list(values: list[str]) -> list[str]:
    cleaned = [item for item in (value.strip() for value in values) if item]
    return cleaned[:MAX_LIST_ITEMS]


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value[:MAX_ITEM_CHARS] or None


def _clean_mapping(values: dict[str, str]) -> dict[str, str]:
    cleaned = {
        str(key).strip()[:MAX_ITEM_CHARS]: str(val).strip()[:MAX_ITEM_CHARS]
        for key, val in values.items()
        if str(key).strip() and str(val).strip()
    }
    return dict(list(cleaned.items())[:MAX_LIST_ITEMS])


def validate_extraction(payload: Any) -> ExtractedPreferences | None:
    """Validate one raw extraction payload.

    Returns the normalized preferences, or ``None`` when the payload is not
    an object with the expected field types (malformed ⇒ discard, ADR-007).
    Extra unknown keys are ignored; blank entries are dropped; over-long
    values are capped.
    """
    if not isinstance(payload, dict):
        return None
    try:
        prefs = ExtractedPreferences.model_validate(payload)
    except ValidationError:
        return None
    prefs.interests = _clean_list(prefs.interests)
    prefs.likes = _clean_list(prefs.likes)
    prefs.dislikes = _clean_list(prefs.dislikes)
    prefs.notes = [item[:MAX_ITEM_CHARS] for item in _clean_list(prefs.notes)]
    prefs.sizes = _clean_mapping(prefs.sizes)
    prefs.price_band = _clean_text(prefs.price_band)
    return prefs
