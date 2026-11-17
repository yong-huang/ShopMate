"""Composition-root wiring (T05/T06): full registry + retrieval index lifespan.

Seam: ``create_app`` — the app must build the graph over the FULL tool
registry (store info + the three catalog tools + the four cart tools) and
construct the hybrid retrieval index once at startup, so chat turns search
the real catalog and share the shopper's cart with the storefront.
"""

import uuid

from app.gateway.events import FinishEvent, TokenDeltaEvent, ToolCallEvent
from app.gateway.tools import ToolCall

from .agent.mocks import ScriptedChatClient
from .test_chat_api import make_settings, parse_sse, stream_chat


async def test_app_builds_index_and_full_registry_at_startup(tmp_path):
    from app.main import create_app

    settings = make_settings(tmp_path).model_copy(update={"embedding_provider": "none"})
    app = create_app(settings=settings, chat_client=ScriptedChatClient([]))
    async with app.router.lifespan_context(app):
        index = app.state.retrieval_index
        assert index is not None
        assert index.vectors_enabled is False  # embedding_provider=none
        tool_names = [spec.name for spec in app.state.tool_registry.specs()]
        assert tool_names == [
            "get_store_info",
            "search_products",
            "get_product_details",
            "compare_products",
            "add_to_cart",
            "get_cart",
            "remove_from_cart",
            "checkout",
        ]
        hits = await index.search("wireless headphones", limit=3)
        assert hits and hits[0].product.id == "ELE-001"


async def test_chat_turn_searches_through_the_wired_registry(tmp_path):
    """The catalog tool is registered at app level, so search executes."""
    from app.main import create_app

    settings = make_settings(tmp_path).model_copy(update={"embedding_provider": "none"})
    fake = ScriptedChatClient(
        [
            [
                TokenDeltaEvent(text="Searching."),
                ToolCallEvent(
                    call=ToolCall(
                        id="c1",
                        name="search_products",
                        arguments={"query": "wireless headphones", "limit": 3},
                    )
                ),
                FinishEvent(finish_reason="tool_calls"),
            ],
            [
                TokenDeltaEvent(text="The Aurora X9 is a strong pick."),
                FinishEvent(finish_reason="stop"),
            ],
        ]
    )
    app = create_app(settings=settings, chat_client=fake)
    async with app.router.lifespan_context(app):
        response = await stream_chat(
            app, {"session_id": uuid.uuid4().hex, "message": "recommend headphones"}
        )

    events = parse_sse(response.text)
    tool_calls = [event for event in events if event["event"] == "tool_call"]
    assert tool_calls and tool_calls[0]["data"]["name"] == "search_products"
    cards = [event for event in events if event["event"] == "product_card"]
    assert cards, "search results must stream as validated product cards"
    assert cards[0]["data"]["product_id"] == "ELE-001"
