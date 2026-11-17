"""Profile merge semantics (System Design 3.6, ADR-007).

Seam: ``app.profile.merge.merge_extraction`` — per-field last-write-wins
toward the newer session, with a source-session stamp per merged field.
Interactions with the profile payload shape here also pin the dict contract
used by the API, the renderer, and the store.
"""

from datetime import UTC, datetime

from app.profile.merge import merge_extraction
from app.profile.schemas import ExtractedPreferences

T1 = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 17, 11, 0, tzinfo=UTC)


def _prefs(**overrides) -> ExtractedPreferences:
    payload = {
        "interests": [],
        "price_band": None,
        "sizes": {},
        "likes": [],
        "dislikes": [],
        "notes": [],
    }
    payload.update(overrides)
    return ExtractedPreferences.model_validate(payload)


def test_first_extraction_seeds_fields_and_sources():
    merged = merge_extraction(
        None,
        _prefs(interests=["running shoes"], price_band="$50-120"),
        session_id="sess-1",
        at=T1,
    )
    assert merged["interests"] == ["running shoes"]
    assert merged["price_band"] == "$50-120"
    assert merged["sizes"] == {}
    assert merged["field_sources"]["interests"] == {
        "session_id": "sess-1",
        "at": "2026-11-17T10:00:00+00:00",
    }
    assert merged["updated_at"] == "2026-11-17T10:00:00+00:00"


def test_newer_session_overwrites_conflicting_fields():
    existing = merge_extraction(
        None, _prefs(interests=["yoga"], likes=["Lululemon"]), "sess-1", T1
    )
    merged = merge_extraction(
        existing,
        _prefs(interests=["trail running"]),
        session_id="sess-2",
        at=T2,
    )
    assert merged["interests"] == ["trail running"]
    assert merged["likes"] == ["Lululemon"]  # untouched field keeps old value
    assert merged["field_sources"]["interests"]["session_id"] == "sess-2"
    assert merged["field_sources"]["likes"]["session_id"] == "sess-1"


def test_empty_fields_in_extraction_do_not_clobber_existing_values():
    existing = merge_extraction(
        None, _prefs(interests=["yoga"], price_band="$50-120"), "sess-1", T1
    )
    merged = merge_extraction(
        existing, _prefs(interests=[], price_band=None), "sess-2", T2
    )
    assert merged["interests"] == ["yoga"]
    assert merged["price_band"] == "$50-120"
    # No write happened, so the source stamp still points at the old session.
    assert merged["field_sources"]["interests"]["session_id"] == "sess-1"


def test_explicit_dislike_overwrites_previous_like_field():
    existing = merge_extraction(None, _prefs(dislikes=["wool"]), "sess-1", T1)
    merged = merge_extraction(
        existing, _prefs(dislikes=["leather", "wool blends"]), "sess-2", T2
    )
    assert merged["dislikes"] == ["leather", "wool blends"]


def test_interaction_log_passes_through_merge_untouched():
    existing = {
        "interests": ["yoga"],
        "price_band": None,
        "sizes": {},
        "likes": [],
        "dislikes": [],
        "notes": [],
        "field_sources": {},
        "updated_at": "2026-11-17T09:00:00+00:00",
        "interaction_log": [{"type": "view", "product_id": "P-1", "at": "..."}],
    }
    merged = merge_extraction(existing, _prefs(notes=["budget conscious"]), "sess-9", T2)
    assert merged["interaction_log"] == existing["interaction_log"]
    assert merged["notes"] == ["budget conscious"]
