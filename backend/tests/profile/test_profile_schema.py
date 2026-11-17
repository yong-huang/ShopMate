"""Extraction schema validation (System Design 3.6, ADR-007).

Seam: ``app.profile.schemas.validate_extraction`` — the schema gate between
the extraction LLM's free-text JSON and the profile store. A payload either
validates into ``ExtractedPreferences`` or is rejected as ``None`` so the
caller can discard the whole extraction (never corrupt the profile).
"""

from app.profile.schemas import validate_extraction


def test_valid_payload_parses_with_defaults_for_missing_fields():
    prefs = validate_extraction(
        {"interests": ["running shoes"], "price_band": "$50-120"}
    )
    assert prefs is not None
    assert prefs.interests == ["running shoes"]
    assert prefs.price_band == "$50-120"
    assert prefs.sizes == {}
    assert prefs.likes == []
    assert prefs.dislikes == []
    assert prefs.notes == []


def test_all_schema_fields_round_trip():
    prefs = validate_extraction(
        {
            "interests": ["trail running", "yoga"],
            "price_band": "$50-120",
            "sizes": {"shoes": "US 10", "shirt": "M"},
            "likes": ["Nike"],
            "dislikes": ["leather"],
            "notes": ["prefers free shipping"],
        }
    )
    assert prefs is not None
    assert prefs.interests == ["trail running", "yoga"]
    assert prefs.sizes == {"shoes": "US 10", "shirt": "M"}
    assert prefs.likes == ["Nike"]
    assert prefs.dislikes == ["leather"]
    assert prefs.notes == ["prefers free shipping"]


def test_wrong_type_for_field_discards_whole_extraction():
    # interests must be a list of strings, never a bare string (ADR-007).
    assert validate_extraction({"interests": "running shoes"}) is None


def test_non_object_payload_discarded():
    assert validate_extraction(["running", "yoga"]) is None
    assert validate_extraction("just text") is None
    assert validate_extraction(None) is None


def test_unknown_extra_fields_are_ignored_not_fatal():
    prefs = validate_extraction(
        {"interests": ["yoga"], "mood": "happy", "confidence": 0.9}
    )
    assert prefs is not None
    assert prefs.interests == ["yoga"]


def test_blank_entries_are_dropped_during_normalization():
    prefs = validate_extraction(
        {"interests": ["", "   ", "yoga"], "notes": ["", "prefers free shipping"]}
    )
    assert prefs is not None
    assert prefs.interests == ["yoga"]
    assert prefs.notes == ["prefers free shipping"]


def test_whitespace_is_stripped_and_empty_strings_normalize_to_none():
    prefs = validate_extraction(
        {"interests": ["  yoga  "], "price_band": "  ", "sizes": {"shoes": " US 10 "}}
    )
    assert prefs is not None
    assert prefs.interests == ["yoga"]
    assert prefs.price_band is None
    assert prefs.sizes == {"shoes": "US 10"}


def test_oversized_values_are_capped_not_stored_verbatim():
    prefs = validate_extraction(
        {
            "interests": [f"interest-{i}" for i in range(50)],
            "notes": ["x" * 500],
        }
    )
    assert prefs is not None
    assert len(prefs.interests) <= 20
    assert all(len(item) <= 200 for item in prefs.notes)
