"""Cart summary prompt block rendering (System Design 3.2/3.3).

Seam: ``app.cart.render.render_cart_summary_text`` — the CART_SUMMARY block
the chat endpoint compiles into Maya's system prompt each turn.
"""

from app.cart.render import render_cart_summary_text


def _summary(**overrides):
    payload = {
        "items": [
            {
                "cart_item_id": 1,
                "product_id": "TST-001",
                "name_en": "Trailblazer Rain Jacket",
                "name_zh": "拓路者雨衣",
                "unit_price": 50.0,
                "currency": "USD",
                "quantity": 2,
                "variant": {"size": "M"},
                "line_total": 100.0,
                "image": "x.svg",
            }
        ],
        "item_count": 2,
        "total": 100.0,
        "currency": "USD",
    }
    payload.update(overrides)
    return payload


def test_empty_or_absent_cart_renders_as_empty_string():
    assert render_cart_summary_text(None) == ""
    assert render_cart_summary_text({"items": [], "item_count": 0, "total": 0.0}) == ""


def test_cart_block_lists_items_variants_and_total():
    text = render_cart_summary_text(_summary())
    assert "2 item(s) in the cart:" in text
    assert "- Trailblazer Rain Jacket (TST-001) x2 (size: M) — $100.00" in text
    assert "Cart total: $100.00 USD" in text


def test_long_carts_are_capped():
    items = [_summary()["items"][0] for _ in range(40)]
    text = render_cart_summary_text({"items": items, "item_count": 80, "total": 4000.0})
    assert len(text) <= 1200
