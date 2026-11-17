"""Profile block rendering for prompt injection (System Design 3.6).

Seam: ``app.profile.render.render_profile_block`` — the compact serialization
of a profile payload into the ``PROFILE_BLOCK`` seam of Maya's prompt, capped
so the injected context stays ~300 tokens.
"""

from app.profile.render import render_profile_block


def _full_profile() -> dict:
    return {
        "interests": ["trail running", "yoga"],
        "price_band": "$50-120",
        "sizes": {"shoes": "US 10", "shirt": "M"},
        "likes": ["Nike"],
        "dislikes": ["leather"],
        "notes": ["prefers free shipping"],
        "field_sources": {},
        "updated_at": "2026-11-17T10:00:00+00:00",
        "interaction_log": [
            {"type": "view", "product_id": "P-1001", "at": "2026-11-17T09:00:00+00:00"},
            {"type": "added_to_cart", "product_id": "P-1002", "at": "2026-11-17T09:05:00+00:00"},
        ],
    }


def test_empty_profile_renders_empty_string():
    assert render_profile_block({}) == ""
    assert (
        render_profile_block(
            {
                "interests": [],
                "price_band": None,
                "sizes": {},
                "likes": [],
                "dislikes": [],
                "notes": [],
                "interaction_log": [],
            }
        )
        == ""
    )


def test_profile_renders_one_compact_line_per_known_field():
    block = render_profile_block(_full_profile())
    assert "- Interests: trail running, yoga" in block
    assert "- Price band: $50-120" in block
    assert "- Sizes: shoes=US 10, shirt=M" in block
    assert "- Likes: Nike" in block
    assert "- Dislikes: leather" in block
    assert "- Notes: prefers free shipping" in block


def test_interaction_log_summarizes_recent_events():
    block = render_profile_block(_full_profile())
    assert "Recent activity:" in block
    assert "viewed P-1001" in block
    assert "added_to_cart P-1002" in block


def test_render_is_capped_at_max_chars():
    profile = _full_profile()
    profile["interests"] = [f"interest number {i} with words" for i in range(60)]
    profile["notes"] = ["note " * 100] * 20
    block = render_profile_block(profile, max_chars=1200)
    assert len(block) <= 1200
    assert block.rstrip().endswith("…")  # honest truncation marker


def test_render_respects_smaller_explicit_cap():
    block = render_profile_block(_full_profile(), max_chars=80)
    assert len(block) <= 80
