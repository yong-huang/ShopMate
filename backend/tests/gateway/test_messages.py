"""Conversation wire-format contract (System Design 3.1 / 3.2 tool hops).

Seam: ``ChatMessage.to_wire`` — the public serialization the agent layer
(T04) relies on to replay a full tool hop: system + user, assistant tool
request, tool result, and follow-up assistant text, in OpenAI-compatible
shape.
"""

from app.gateway import ChatMessage, ToolCall


def test_tool_hop_conversation_serializes_to_openai_wire_format() -> None:
    messages = [
        ChatMessage(role="system", content="You are Maya."),
        ChatMessage(role="user", content="add the trail shoes"),
        ChatMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="add_to_cart",
                    arguments={"product_id": "p1", "quantity": 1},
                )
            ],
        ),
        ChatMessage(role="tool", tool_call_id="call_1", content='{"cart_total": 0}'),
        ChatMessage(role="assistant", content="Added to your cart."),
    ]

    wire = [message.to_wire() for message in messages]

    assert wire == [
        {"role": "system", "content": "You are Maya."},
        {"role": "user", "content": "add the trail shoes"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "add_to_cart",
                        "arguments": '{"product_id": "p1", "quantity": 1}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"cart_total": 0}'},
        {"role": "assistant", "content": "Added to your cart."},
    ]
