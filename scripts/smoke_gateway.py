"""Live smoke test for the model gateway (optional; never run in CI/tests).

Dials the *real* configured provider (backend/.env or environment) through
the production ``create_chat_client`` facade and prints the ChatEvent stream.
Useful for verifying GLM / OpenAI / Ollama credentials and tool calling.

Usage:
    cd backend && uv run python ../scripts/smoke_gateway.py
    cd backend && uv run python ../scripts/smoke_gateway.py --no-tools
    cd backend && uv run python ../scripts/smoke_gateway.py --prompt "hi"

Exit codes: 0 ok, 2 config error, 3 provider HTTP error, 4 degradation.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import get_settings  # noqa: E402
from app.gateway import (  # noqa: E402
    ChatMessage,
    GatewayConfigError,
    ProviderHTTPError,
    ToolCallDegradationError,
    ToolSpec,
    create_chat_client,
)

SEARCH_PRODUCTS = ToolSpec(
    name="search_products",
    description="Hybrid catalog search. Call this whenever the user asks "
    "about or wants product recommendations.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Free-text search query"},
            "limit": {"type": "integer", "description": "Max results (<= 8)"},
        },
        "required": ["query"],
    },
)


async def run(prompt: str, with_tools: bool) -> int:
    settings = get_settings()
    key_shown = "set" if settings.llm_api_key else "unset"
    print(
        f"provider={settings.llm_provider} "
        f"model={settings.ollama_chat_model if settings.llm_provider == 'ollama' else settings.llm_model} "
        f"api_key={key_shown} "
        f"degradation_attempts={settings.llm_tool_degradation_attempts}"
    )

    tools = [SEARCH_PRODUCTS] if with_tools else None
    client = create_chat_client(settings)
    stream = client.stream(
        [ChatMessage(role="user", content=prompt)], tools=tools
    )
    async for event in stream:
        if event.type == "token_delta":
            print(event.text, end="", flush=True)
        elif event.type == "tool_call":
            print(f"\n[tool_call] {event.call.name}({json.dumps(event.call.arguments)})")
        elif event.type == "finish":
            print(f"\n[finish] {event.finish_reason}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Gateway live smoke test")
    parser.add_argument(
        "--no-tools", action="store_true", help="plain completion, no tool schemas"
    )
    parser.add_argument(
        "--prompt",
        default="Recommend a pair of trail running shoes under $120.",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.prompt, with_tools=not args.no_tools))
    except GatewayConfigError as error:
        print(f"config error: {error}", file=sys.stderr)
        return 2
    except ProviderHTTPError as error:
        print(f"provider error: {error}", file=sys.stderr)
        return 3
    except ToolCallDegradationError as error:
        print(f"degradation: {error}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
