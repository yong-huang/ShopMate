"""Maya system prompt compilation (System Design 3.2).

The persona and rules live in ``backend/app/agent/prompts/maya.md`` and are
compiled once per turn: the static persona + rules are combined with the
context blocks (shopper profile, cart summary, catalog overview). The blocks
are injection seams — this ticket renders them empty, T06 (cart) and T07
(profile) fill them.
"""

from functools import lru_cache
from pathlib import Path

SECTIONS = (
    ("PROFILE_BLOCK", "## Shopper profile"),
    ("CART_SUMMARY", "## Cart summary"),
    ("CATALOG_OVERVIEW", "## Catalog overview"),
)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "maya.md"


@lru_cache(maxsize=1)
def _load_template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def maya_prompt_template() -> str:
    """The raw ``maya.md`` template (persona + rules + seams)."""
    return _load_template()


def compile_maya_prompt(
    *,
    profile_block: str | None = None,
    cart_summary: str | None = None,
    catalog_overview: str | None = None,
) -> str:
    """Compile the per-turn system prompt.

    Empty context blocks render as nothing at all — no dangling section
    headers — so the prompt stays clean until T06/T07 provide real content.
    """
    blocks = {
        "PROFILE_BLOCK": (profile_block or "").strip(),
        "CART_SUMMARY": (cart_summary or "").strip(),
        "CATALOG_OVERVIEW": (catalog_overview or "").strip(),
    }
    prompt = maya_prompt_template()
    for key, header in SECTIONS:
        empty_section = f"{header}\n\n{{{{{key}}}}}\n"
        if blocks[key]:
            filled_section = f"{header}\n\n{blocks[key]}\n"
            prompt = prompt.replace(empty_section, filled_section)
        else:
            prompt = prompt.replace(empty_section, "")
    return prompt
