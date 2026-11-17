"""Guardrail validation in the agent graph (System Design 3.5, PRD FR-8).

Seam: ``build_agent_graph`` + ``stream_agent_turn`` with the ChatClient faked
(``ScriptedChatClient``) and the *real* committed catalog behind the tools —
so "truthful" means equal to a known catalog record, not a fixture. Every
test exercises the retry-once/strip failure policy end to end on the graph.
"""

import uuid

import pytest
from langchain_core.messages import HumanMessage

from app.agent.catalog_tools import register_catalog_tools
from app.agent.graph import (
    AgentToken,
    AgentValidation,
    build_agent_graph,
    close_checkpointer,
    open_checkpointer,
    stream_agent_turn,
)
from app.agent.tools import tool_registry
from app.catalog.adapter import JsonCatalogAdapter
from app.gateway.events import FinishEvent, TokenDeltaEvent, ToolCallEvent
from app.gateway.tools import ToolCall
from app.retrieval.index import RetrievalIndex

from .mocks import ScriptedChatClient

ADAPTER = JsonCatalogAdapter()

_OPEN_SAVERS: list = []


@pytest.fixture(autouse=True)
async def _close_checkpointers():
    _OPEN_SAVERS.clear()
    yield
    for saver in _OPEN_SAVERS:
        await close_checkpointer(saver)
    _OPEN_SAVERS.clear()


async def _new_graph(tmp_path, client):
    index = await RetrievalIndex.build(ADAPTER)
    registry = tool_registry()
    register_catalog_tools(registry, ADAPTER, index)
    saver = await open_checkpointer(tmp_path / "ck.db")
    _OPEN_SAVERS.append(saver)
    return build_agent_graph(
        client=client, tools=registry, checkpointer=saver, adapter=ADAPTER
    )


def _card_block(product_id: str, price: float, rating: float) -> str:
    return (
        "```product-card\n"
        f'{{"product_id": "{product_id}", "price": {price}, "rating": {rating}}}\n'
        "```"
    )


def _claim(product_id: str) -> dict:
    product = ADAPTER.get_product(product_id)
    return {"product_id": product_id, "price": product.price, "rating": product.rating}


def _search_stream(query: str) -> list:
    return [
        TokenDeltaEvent(text="Let me find some options."),
        ToolCallEvent(
            call=ToolCall(
                id="call_search",
                name="search_products",
                arguments={"query": query, "limit": 3},
            )
        ),
        FinishEvent(finish_reason="tool_calls"),
    ]


def _card_text(*blocks: str) -> list:
    body = "Here is what I found:\n" + "\n".join(blocks)
    return [TokenDeltaEvent(text=body), FinishEvent(finish_reason="stop")]


async def _run(graph, message: str) -> list:
    return [event async for event in stream_agent_turn(graph, uuid.uuid4().hex, message)]


def _validation(events: list) -> AgentValidation:
    found = [event for event in events if isinstance(event, AgentValidation)]
    assert len(found) == 1, "exactly one validation verdict per turn"
    return found[0]


async def _final_state(graph, thread_id: str):
    return await graph.aget_state({"configurable": {"thread_id": thread_id}})


async def test_guidance_turn_with_truthful_blocks_validates_cards(tmp_path):
    """Happy guided-discovery path: search → truthful card blocks → cards pass."""
    client = ScriptedChatClient(
        [
            _search_stream("noise cancelling headphones for travel"),
            _card_text(_card_block(**_claim("ELE-001")), _card_block(**_claim("ELE-002"))),
        ]
    )
    graph = await _new_graph(tmp_path, client)
    thread_id = uuid.uuid4().hex

    events = [
        event async for event in stream_agent_turn(graph, thread_id, "travel headphones?")
    ]

    verdict = _validation(events)
    assert verdict.outcome == "passed"
    assert verdict.failed_product_ids == []
    assert verdict.card_count == 2

    state = await _final_state(graph, thread_id)
    cards = state.values["validated_cards"]
    assert [card["product_id"] for card in cards] == ["ELE-001", "ELE-002"]
    for card in cards:
        product = ADAPTER.get_product(card["product_id"])
        assert card["price"] == product.price
        assert card["rating"] == product.rating
        assert card["image_url"] == f"/static/catalog/images/{card['product_id']}.svg"


async def test_fabricated_product_id_retries_once_then_strips(tmp_path):
    """Fabricated id: first pass fails, corrective system message, retry, strip."""
    client = ScriptedChatClient(
        [
            _card_text(_card_block("FAKE-999", 49.99, 4.9)),
            _card_text("Sorry — I don't have a product with that id in the catalog."),
        ]
    )
    graph = await _new_graph(tmp_path, client)
    thread_id = uuid.uuid4().hex

    events = [
        event async for event in stream_agent_turn(graph, thread_id, "buy me thing X")
    ]

    # Exactly one corrective retry: two LLM calls, the second carrying the
    # corrective system message naming the failing card.
    assert len(client.calls) == 2
    corrective = [
        message.content
        for message in client.calls[1].messages
        if message.role == "system"
    ]
    assert any("FAKE-999" in content for content in corrective)
    assert any("non-catalog" in content for content in corrective)

    verdict = _validation(events)
    assert verdict.outcome == "stripped"
    assert verdict.failed_product_ids == ["FAKE-999"]
    assert verdict.card_count == 0

    state = await _final_state(graph, thread_id)
    assert state.values["validated_cards"] == []
    # Honest path: the turn still ends with streamed text (no card events).
    assert any(isinstance(event, AgentToken) for event in events)


async def test_retry_with_truthful_block_recovers_the_card(tmp_path):
    """First pass fabricated, retry truthful → outcome 'retried', card renders."""
    client = ScriptedChatClient(
        [
            _card_text(_card_block("FAKE-999", 49.99, 4.9)),
            _card_text(_card_block(**_claim("ELE-001"))),
        ]
    )
    graph = await _new_graph(tmp_path, client)
    thread_id = uuid.uuid4().hex

    events = [
        event async for event in stream_agent_turn(graph, thread_id, "recommend headphones")
    ]

    verdict = _validation(events)
    assert verdict.outcome == "retried"
    # Pre-strip audit: the pass-1 fabricated id stays visible for eval.
    assert verdict.failed_product_ids == ["FAKE-999"]
    assert verdict.card_count == 1

    state = await _final_state(graph, thread_id)
    assert [card["product_id"] for card in state.values["validated_cards"]] == ["ELE-001"]


async def test_price_mismatch_is_rejected_and_stripped_after_retry(tmp_path):
    """Claimed price below catalog value never renders, even after retry."""
    client = ScriptedChatClient(
        [
            _card_text(_card_block("ELE-001", 99.99, 4.7)),
            _card_text(_card_block("ELE-001", 99.99, 4.7)),
        ]
    )
    graph = await _new_graph(tmp_path, client)
    thread_id = uuid.uuid4().hex

    events = [
        event async for event in stream_agent_turn(graph, thread_id, "how much is ELE-001?")
    ]

    verdict = _validation(events)
    assert verdict.outcome == "stripped"
    assert verdict.failed_product_ids == ["ELE-001"]

    state = await _final_state(graph, thread_id)
    assert state.values["validated_cards"] == []


async def test_tool_cards_render_even_without_model_blocks(tmp_path):
    """Ground truth fallback: search results render as cards with match hints."""
    client = ScriptedChatClient(
        [
            _search_stream("noise cancelling headphones for travel"),
            _card_text("My picks are above — the Aurora X9 is great for flights."),
        ]
    )
    graph = await _new_graph(tmp_path, client)
    thread_id = uuid.uuid4().hex

    events = [
        event async for event in stream_agent_turn(graph, thread_id, "travel headphones?")
    ]

    verdict = _validation(events)
    assert verdict.outcome == "passed"

    state = await _final_state(graph, thread_id)
    cards = state.values["validated_cards"]
    assert cards, "tool-attached cards must render even without model blocks"
    assert any(card["match_hint"] for card in cards)
    assert any(card["product_id"] == "ELE-001" for card in cards)


async def test_comparison_tool_result_lands_in_state_for_the_sse_layer(tmp_path):
    client = ScriptedChatClient(
        [
            [
                TokenDeltaEvent(text="Comparing now."),
                ToolCallEvent(
                    call=ToolCall(
                        id="call_compare",
                        name="compare_products",
                        arguments={"product_ids": ["ELE-001", "ELE-002"]},
                    )
                ),
                FinishEvent(finish_reason="tool_calls"),
            ],
            _card_text("Choose ELE-001 if battery life matters most."),
        ]
    )
    graph = await _new_graph(tmp_path, client)
    thread_id = uuid.uuid4().hex

    events = [
        event async for event in stream_agent_turn(graph, thread_id, "compare them")
    ]

    assert _validation(events).outcome == "passed"
    state = await _final_state(graph, thread_id)
    comparison = state.values["comparison"]
    assert comparison["product_ids"] == ["ELE-001", "ELE-002"]
    assert comparison["matrix"]["columns"] == ["ELE-001", "ELE-002"]


async def test_validation_state_resets_between_turns(tmp_path):
    """Cards from turn 1 must not leak into turn 2's validation verdict."""
    client = ScriptedChatClient(
        [
            _card_text(_card_block(**_claim("ELE-001"))),
            _card_text("A plain answer with no product references."),
        ]
    )
    graph = await _new_graph(tmp_path, client)
    thread_id = uuid.uuid4().hex

    _ = [event async for event in stream_agent_turn(graph, thread_id, "recommend headphones")]
    events = [event async for event in stream_agent_turn(graph, thread_id, "thanks!")]

    verdict = _validation(events)
    assert verdict.outcome == "passed"
    assert verdict.card_count == 0
    state = await _final_state(graph, thread_id)
    assert state.values["validated_cards"] == []


async def test_validate_node_is_passthrough_without_cards(tmp_path):
    """Plain conversational turn: no validation verdict noise, straight END."""
    client = ScriptedChatClient([_card_text("Hello! How can I help?")])
    graph = await _new_graph(tmp_path, client)

    events = [
        event async for event in stream_agent_turn(graph, uuid.uuid4().hex, "hi")
    ]

    verdict = _validation(events)
    assert verdict.outcome == "passed"
    assert verdict.card_count == 0
