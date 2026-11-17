"""Compact cart summary block for Maya's prompt (System Design 3.2/3.3).

One line per cart item plus the total, capped so a full cart stays cheap to
inject. An empty (or absent) cart renders as ``""`` — ``compile_maya_prompt``
drops the section entirely.
"""

from typing import Any

# ~300 tokens at ~4 chars/token; a full cart stays far below this.
DEFAULT_MAX_CHARS = 1200


def render_cart_summary_text(
    summary: dict[str, Any] | None, max_chars: int = DEFAULT_MAX_CHARS
) -> str:
    """Serialize a cart payload into the ``CART_SUMMARY`` context text."""
    items = (summary or {}).get("items") or []
    if not items:
        return ""
    lines = [f"{(summary or {}).get('item_count', 0)} item(s) in the cart:"]
    for item in items:
        variant = item.get("variant") or {}
        suffix = ""
        if variant:
            details = ", ".join(f"{key}: {value}" for key, value in sorted(variant.items()))
            suffix = f" ({details})"
        lines.append(
            f"- {item['name_en']} ({item['product_id']}) x{item['quantity']}"
            f"{suffix} — ${item['line_total']:.2f}"
        )
    total = (summary or {}).get("total", 0.0)
    currency = (summary or {}).get("currency", "USD")
    lines.append(f"Cart total: ${total:.2f} {currency}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text
