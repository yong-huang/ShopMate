"""Maya system prompt compilation (System Design 3.2, PRD 3.3).

Seam: ``app.agent.prompts.compile_maya_prompt`` — loads the persona + rules
from ``backend/app/agent/prompts/maya.md`` and compiles it per turn with the
context blocks (profile / cart summary / catalog overview) that later tickets
(T06/T07) fill in. This ticket pins the persona, the five rules, and the
injection seams.
"""

from app.agent.prompts import compile_maya_prompt, maya_prompt_template

RULE_MARKERS = [
    "Mirror the user's language",  # rule 1
    "search_products",  # rule 2 (recommend only tool-returned products)
    "2 clarifying questions",  # rule 3
    "Never state a price or spec",  # rule 4
    "Off-scope",  # rule 5
]


def test_prompt_template_file_carries_persona_and_all_five_rules():
    template = maya_prompt_template()
    assert "Maya" in template
    for marker in RULE_MARKERS:
        assert marker in template, f"missing rule marker: {marker}"


def test_default_compilation_contains_persona_and_rules_without_empty_sections():
    prompt = compile_maya_prompt()
    assert "Maya" in prompt
    for marker in RULE_MARKERS:
        assert marker in prompt
    # Seams render nothing (no dangling headers) when no context is given.
    for seam in ("{{", "}}"):
        assert seam not in prompt
    for header in ("## Shopper profile", "## Cart summary", "## Catalog overview"):
        assert header not in prompt


def test_compilation_injects_profile_cart_and_catalog_blocks():
    prompt = compile_maya_prompt(
        profile_block="Known profile: prefers trail running, budget <$120.",
        cart_summary="Cart: 1 x Trail Shoe A ($89).",
        catalog_overview="Categories: Electronics, Home & Kitchen.",
    )
    assert "prefers trail running" in prompt
    assert "1 x Trail Shoe A" in prompt
    assert "Home & Kitchen" in prompt
    assert "{{" not in prompt


def test_prompt_documents_the_product_card_block_format():
    """T05: rule 2 must teach the model the exact card-block contract (3.5)."""
    template = maya_prompt_template()
    assert "```product-card" in template
    for field in ("product_id", "price", "rating"):
        assert field in template
    assert template.count("product-card") >= 2  # fence open + close documented
