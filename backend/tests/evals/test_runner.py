"""Runner integration tests: scripted multi-turn SSE runs through the real app.

Seam: the live app (``create_app`` on a temp SQLite DB) with the ChatClient
faked at the gateway — hand-scripted streams via ``ScriptedChatClient`` (the
same mock the backend tests use) prove the runner mechanics end to end.
All hermetic, no provider contact. Product ids/prices in the scripts are the
real committed catalog records (ELE-001 Aurora X9 $249.99), so tools and the
guardrail behave exactly as in production.
"""

import pytest

from app.db import Profile
from app.gateway.events import FinishEvent, TokenDeltaEvent, ToolCallEvent
from app.gateway.tools import ToolCall
from evals.runner.capture import TurnLogCapture
from evals.runner.fake_model import ScriptedJudge
from evals.runner.harness import eval_app_generator, run_scenario, seed_profile
from evals.runner.session import ChatSession, parse_sse
from evals.schema import Scenario, ScenarioAction, Turn, TurnExpect, scenario_from_dict
from tests.agent.mocks import ScriptedChatClient


def scripted_add_flow(*, bad_card: bool = False):
    """Script: add-to-cart tool flow, then a confirm reply (retry if bad card).

    With ``bad_card`` the confirm reply embeds a fabricated ``product-card``
    block: the guardrail demands one corrective retry, so the third stream is
    the clean retry reply.
    """
    first = [
        TokenDeltaEvent(text="Adding the Aurora X9 now."),
        ToolCallEvent(
            call=ToolCall(id="c1", name="add_to_cart", arguments={"product_id": "ELE-001"})
        ),
        FinishEvent(finish_reason="tool_calls"),
    ]
    second_text = "Added — your cart has 1 item."
    streams = [first]
    if bad_card:
        second_text += (
            '\n\n```product-card\n{"product_id": "XXX-999", "price": 1.0, "rating": 5.0}\n```'
        )
    streams.append([TokenDeltaEvent(text=second_text), FinishEvent(finish_reason="stop")])
    if bad_card:
        streams.append(
            [
                TokenDeltaEvent(text="Added — your cart has 1 item."),
                FinishEvent(finish_reason="stop"),
            ]
        )
    return ScriptedChatClient(streams)


ADD_SCENARIO = Scenario(
    id="cart-add-en",
    category="cart",
    lang="en",
    title="add to cart",
    profile=None,
    seed_cart=[],
    judge_min=3.0,
    turns=[
        Turn(
            user="add the Aurora X9 headphones",
            expect=TurnExpect(
                expected_tools_subset=["add_to_cart"],
                cart_expected={"items": [{"product_id": "ELE-001"}], "item_count": 1},
                max_cards=0,
            ),
        )
    ],
)


@pytest.fixture
async def open_app(tmp_path):
    opened = []

    async def _open(chat_client=None):
        agen = eval_app_generator(chat_client=chat_client)
        app = await agen.__anext__()
        opened.append(agen)
        return app

    yield _open
    for agen in opened:
        try:
            await agen.__anext__()
        except StopAsyncIteration:
            pass


@pytest.mark.asyncio
async def test_run_scenario_add_flow_end_to_end(open_app):
    fake = scripted_add_flow()
    app = await open_app(chat_client=fake)
    with TurnLogCapture() as capture:
        result = await run_scenario(
            ADD_SCENARIO, app, ScriptedJudge(), capture, catalog=app.state.catalog
        )

    assert result.passed, result.failures
    assert result.judge_overall == pytest.approx(5.0)
    turn = result.turns[0]
    assert turn.reply == "Adding the Aurora X9 now.Added — your cart has 1 item."
    assert [hop["tool"] for hop in turn.tool_hops] == ["add_to_cart"]
    assert turn.tool_hops[0]["ok"] is True
    assert turn.cart_assert is not None
    assert turn.cart_assert["ok"] is True
    assert turn.cart_assert["actual"]["item_count"] == 1
    assert turn.cart_assert["actual"]["total"] == pytest.approx(249.99)
    check_names = {check["name"] for check in turn.checks}
    assert {"stream", "grounding", "pre_strip_audit", "tools", "cart", "language"} <= check_names
    assert fake.exhausted  # exactly the scripted streams were consumed
    assert result.grounding.rendered_card_ids == []
    assert result.grounding.grounding_rate is None  # no cards, no pre-strip


@pytest.mark.asyncio
async def test_run_scenario_fails_on_pre_strip_fabrication(open_app):
    # The guardrail strips the fabricated card (XXX-999) and retries once —
    # the pre-strip audit must still fail the scenario (§3.1 grounding).
    fake = scripted_add_flow(bad_card=True)
    app = await open_app(chat_client=fake)
    with TurnLogCapture() as capture:
        result = await run_scenario(
            ADD_SCENARIO, app, ScriptedJudge(), capture, catalog=app.state.catalog
        )

    assert not result.passed
    assert any("XXX-999" in failure for failure in result.failures)
    turn = result.turns[0]
    assert turn.validation is not None
    # Production semantics (System Design §3.5): the corrective retry ran and
    # the model still produced no valid card (the scripted retry reply carries
    # none), so the turn finalizes as "stripped" with no rendered cards.
    assert turn.validation["outcome"] == "stripped"
    assert "XXX-999" in turn.validation["failed_product_ids"]
    audit_check = next(c for c in turn.checks if c["name"] == "pre_strip_audit")
    assert audit_check["ok"] is False
    # The fabricated card never rendered.
    assert turn.cards == []
    assert result.grounding.fabricated_pre_strip == ["XXX-999"]
    # The guardrail's corrective retry consumed the third scripted stream.
    assert fake.exhausted


@pytest.mark.asyncio
async def test_run_scenario_language_mismatch_fails(open_app):
    scenario = Scenario(
        id="lang-mismatch-zh",
        category="boundary",
        lang="zh",
        title="",
        profile=None,
        seed_cart=[],
        judge_min=3.0,
        turns=[Turn(user="添加极光X9耳机到购物车", expect=TurnExpect(max_cards=0))],
    )
    fake = ScriptedChatClient(
        [
            [
                TokenDeltaEvent(text="Sure, I added the Aurora X9 to your cart in English."),
                FinishEvent(finish_reason="stop"),
            ]
        ]
    )
    app = await open_app(chat_client=fake)
    with TurnLogCapture() as capture:
        result = await run_scenario(
            scenario, app, ScriptedJudge(), capture, catalog=app.state.catalog
        )
    assert not result.passed
    assert any("language" in failure for failure in result.failures)
    assert result.turns[0].language == {"expected": "zh", "detected": "en", "ok": False}


@pytest.mark.asyncio
async def test_judge_failure_fails_scenario(open_app):
    """A judge that returns prose fails the scenario honestly (§2.3)."""
    from evals.judge.client import JudgeError

    class BrokenJudge(ScriptedJudge):
        async def score_turn(self, *args, **kwargs):
            raise JudgeError("judge exploded")

    fake = ScriptedChatClient(
        [[TokenDeltaEvent(text="Hello!"), FinishEvent(finish_reason="stop")]]
    )
    app = await open_app(chat_client=fake)
    scenario = Scenario(
        id="judge-fail-en",
        category="boundary",
        lang="en",
        title="",
        profile=None,
        seed_cart=[],
        judge_min=3.0,
        turns=[Turn(user="hi", expect=TurnExpect(max_cards=0))],
    )
    with TurnLogCapture() as capture:
        result = await run_scenario(scenario, app, BrokenJudge(), capture, catalog=app.state.catalog)
    assert not result.passed
    assert any("judge failed" in failure for failure in result.failures)
    assert result.judge_failed is True
    assert result.turns[0].judge_error is not None


@pytest.mark.asyncio
async def test_clear_profile_action_clears_row(open_app):
    """Profile seed → turn → clear_profile action → profile row is empty (AC6.3)."""
    scenario = Scenario(
        id="profile-clear-zh",
        category="personalization",
        lang="zh",
        title="",
        profile={"interests": ["camping"]},
        seed_cart=[],
        judge_min=3.0,
        turns=[
            Turn(user="你还记得我喜欢什么吗？", expect=TurnExpect(max_cards=0)),
            Turn(user="那随机推荐一个水壶吧", expect=TurnExpect(max_cards=0)),
        ],
        actions=[ScenarioAction(after_turn=1, type="clear_profile")],
    )
    fake = ScriptedChatClient(
        [
            [TokenDeltaEvent(text="记得的，您喜欢露营相关的内容。"), FinishEvent(finish_reason="stop")],
            [TokenDeltaEvent(text="为您随手推荐恒温电水壶。"), FinishEvent(finish_reason="stop")],
        ]
    )
    app = await open_app(chat_client=fake)
    with TurnLogCapture() as capture:
        result = await run_scenario(scenario, app, ScriptedJudge(), capture, catalog=app.state.catalog)
    assert result.passed, result.failures

    async with app.state.sessionmaker() as db:
        profile = await db.get(Profile, result.user_id)
    # Production semantics: DELETE /api/profile removes the row entirely
    # (AC6.3 — nothing, incl. the interaction log, is re-injected afterwards).
    assert profile is None


@pytest.mark.asyncio
async def test_cross_session_extraction_and_seed_profile(open_app):
    """seed_profile + end_session action draining the scripted extraction."""
    scenario = scenario_from_dict(
        {
            "id": "profile-recall-en",
            "category": "personalization",
            "lang": "en",
            "turns": [
                {"user": "I love camping, suggest gear", "expect": {"max_cards": 0}},
                {"user": "any kettle deals?", "expect": {"max_cards": 0}},
            ],
            "actions": [{"after_turn": 1, "type": "end_session"}],
        }
    )
    fake = ScriptedChatClient(
        [
            [TokenDeltaEvent(text="Camping gear it is!"), FinishEvent(finish_reason="stop")],
            # The extraction pass over session 1 (drained before turn 2).
            [
                TokenDeltaEvent(
                    text='{"interests": ["camping"], "price_band": null, "sizes": {}, '
                    '"likes": ["camping"], "dislikes": [], "notes": []}'
                ),
                FinishEvent(finish_reason="stop"),
            ],
            [TokenDeltaEvent(text="Kettles coming right up."), FinishEvent(finish_reason="stop")],
        ]
    )
    app = await open_app(chat_client=fake)
    await seed_profile(app, "seed-user-1", {"price_band": [30, 100], "interests": ["coffee"]})
    async with app.state.sessionmaker() as db:
        seeded = await db.get(Profile, "seed-user-1")
    assert seeded.price_band == "$30-100"  # [min, max] → pipeline label
    assert seeded.interests == ["coffee"]

    with TurnLogCapture() as capture:
        result = await run_scenario(scenario, app, ScriptedJudge(), capture, catalog=app.state.catalog)
    assert result.passed, result.failures
    assert fake.exhausted

    async with app.state.sessionmaker() as db:
        profile = await db.get(Profile, result.user_id)
    assert "camping" in profile.interests  # extraction merged mid-scenario


@pytest.mark.asyncio
async def test_seed_cart_via_rest_api(open_app):
    """seed_cart flows through POST /api/cart/items before the first turn."""
    scenario = Scenario(
        id="seed-cart-en",
        category="cart",
        lang="en",
        title="",
        profile=None,
        seed_cart=[{"product_id": "ELE-001", "quantity": 2}],
        judge_min=3.0,
        turns=[
            Turn(
                user="what's in my cart?",
                expect=TurnExpect(
                    cart_expected={"items": [{"product_id": "ELE-001", "quantity": 2}]}
                ),
            )
        ],
    )
    fake = ScriptedChatClient(
        [[TokenDeltaEvent(text="You have 2 items in your cart."), FinishEvent(finish_reason="stop")]]
    )
    app = await open_app(chat_client=fake)
    with TurnLogCapture() as capture:
        result = await run_scenario(scenario, app, ScriptedJudge(), capture, catalog=app.state.catalog)
    assert result.passed, result.failures
    assert result.turns[0].cart_assert["actual"]["item_count"] == 2


@pytest.mark.asyncio
async def test_parse_sse_frames():
    from app.api.chat import _sse

    raw = _sse("token", {"text": "hi"}) + _sse("done", {"message_id": 7})
    events = parse_sse(raw)
    assert events == [
        {"event": "token", "data": {"text": "hi"}},
        {"event": "done", "data": {"message_id": 7}},
    ]


@pytest.mark.asyncio
async def test_chat_session_reuses_cookie_identity(open_app):
    app = await open_app(
        chat_client=ScriptedChatClient([[TokenDeltaEvent(text="Hello!"), FinishEvent(finish_reason="stop")]])
    )
    session = ChatSession(app)
    assert session.user_id  # deterministic before the first request
    observation = await session.send_turn("hi")
    assert observation["done"] is True
    assert observation["error"] is None
    assert observation["reply"] == "Hello!"
    summary = await session.cart_summary()
    assert summary["items"] == []
    await session.aclose()
