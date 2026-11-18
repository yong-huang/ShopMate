"""The judge scoring client (Eval Plan §3.2).

One chat-completions call per assistant turn at temperature 0 over the
versioned rubric prompt (:mod:`evals.judge.prompt.md`). The judge sees the
user turn, the assistant reply, a tool-call summary, and catalog excerpts of
every product the reply references (for grounding-aware accuracy scoring).
The reply must be the rubric's JSON object; anything else raises
:class:`JudgeError` — a judge failure is recorded per turn and fails the
scenario honestly instead of being silently dropped.

Judge model policy: ``JUDGE_PROVIDER`` / ``JUDGE_MODEL`` env overrides
(:func:`resolve_judge_settings`); both default to the configured default
chat provider, so the baseline judge = the baseline provider.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.catalog.adapter import CatalogAdapter
from app.config import Settings
from app.gateway.client import ChatClient
from app.gateway.errors import GatewayError
from app.gateway.messages import ChatMessage
from app.profile.extract import parse_json_object

PROMPT_PATH = Path(__file__).parent / "prompt.md"

# §3.2 weights — the single source of truth for per-turn weighted scores.
JUDGE_WEIGHTS: dict[str, float] = {
    "relevance": 0.25,
    "accuracy": 0.25,
    "guidance": 0.20,
    "conciseness": 0.15,
    "language": 0.15,
}

JUDGE_TEMPERATURE = 0.0

_VERSION_RE = re.compile(r"^version:\s*(\S+)", re.MULTILINE)

# Catalog ids look like ELE-001 / HOM-016 / APP-003 / SPO-007.
_PRODUCT_ID_RE = re.compile(r"\b[A-Z]{3}-\d{3}\b")


class JudgeError(RuntimeError):
    """The judge reply was missing, unparseable, or schema-invalid."""


@dataclass
class JudgeVerdict:
    """One turn's rubric verdict (§3.2)."""

    scores: dict[str, float]  # dimension -> 1..5
    clarifying_questions: int
    rationale: str

    @property
    def weighted_score(self) -> float:
        """The weighted mean over the five dimensions (0-scale preserved)."""
        return sum(JUDGE_WEIGHTS[dim] * self.scores[dim] for dim in JUDGE_WEIGHTS)

    def to_dict(self) -> dict:
        return {
            "scores": dict(self.scores),
            "clarifying_questions": self.clarifying_questions,
            "rationale": self.rationale,
            "weighted": round(self.weighted_score, 4),
        }


def load_prompt_text(path: Path | None = None) -> str:
    """The rubric prompt body (everything after the ``---`` header block)."""
    text = (path or PROMPT_PATH).read_text(encoding="utf-8")
    if text.startswith("---"):
        # Drop the front-matter header: everything up to the closing
        # ``---`` line (the opening fence sits at position 0, so the first
        # "\n---" occurrence is the closer).
        parts = text.split("\n---", 1)
        if len(parts) == 2:
            return parts[1].strip()
    return text.strip()


def prompt_version(path: Path | None = None) -> str:
    """The prompt version stamped in the header (report metadata)."""
    text = (path or PROMPT_PATH).read_text(encoding="utf-8")
    match = _VERSION_RE.search(text)
    if match is None:
        raise JudgeError(f"judge prompt at {path or PROMPT_PATH} has no version header")
    return match.group(1)


def resolve_judge_settings(settings: Settings) -> Settings:
    """Apply the ``JUDGE_PROVIDER`` / ``JUDGE_MODEL`` overrides to settings.

    Default (no overrides): the configured default chat provider and its
    model — the eval baseline judge is the baseline provider (§3.2 policy).
    """
    updates: dict = {}
    provider = settings.judge_provider or settings.llm_provider
    updates["llm_provider"] = provider
    if settings.judge_model:
        if provider == "ollama":
            updates["ollama_chat_model"] = settings.judge_model
        else:
            updates["llm_model"] = settings.judge_model
    return settings.model_copy(update=updates)


class Judge:
    """Scores assistant turns against the versioned rubric (§3.2)."""

    def __init__(
        self,
        client: ChatClient,
        catalog: CatalogAdapter,
        prompt_text: str | None = None,
    ) -> None:
        self._client = client
        self._catalog = catalog
        self.prompt_text = prompt_text if prompt_text is not None else load_prompt_text()

    # --- context assembly ---------------------------------------------------

    def catalog_excerpts(self, reply_text: str, product_ids: list[str] | None = None) -> str:
        """Compact catalog excerpts for every product the reply references.

        Ids are harvested from the reply text (``XXX-000`` pattern) plus any
        explicit ids the caller passes (rendered card payloads); excerpts
        carry the facts grounding cares about: bilingual name, category,
        price, rating, and key specs.
        """
        ids: list[str] = list(dict.fromkeys(_PRODUCT_ID_RE.findall(reply_text)))
        for product_id in product_ids or []:
            if product_id not in ids:
                ids.append(product_id)
        blocks: list[str] = []
        for product_id in ids:
            product = self._catalog.get_product(product_id)
            if product is None:
                blocks.append(f"{product_id}: NOT IN CATALOG")
                continue
            specs = "; ".join(
                f"{spec['label']}={spec['value']}" if isinstance(spec, dict) else f"{spec.label}={spec.value}"
                for spec in product.specs[:6]
            )
            blocks.append(
                f"{product.id} | {product.name_en} | {product.name_zh} | "
                f"category={product.category} | price={product.price} {product.currency} | "
                f"rating={product.rating} | specs: {specs}"
            )
        return "\n".join(blocks) if blocks else "(no products referenced)"

    # --- scoring --------------------------------------------------------------

    async def score_turn(
        self,
        user_text: str,
        reply_text: str,
        tool_summary: str = "(no tools called)",
        catalog_excerpts: str | None = None,
    ) -> JudgeVerdict:
        """Score one assistant reply; raises :class:`JudgeError` on failure."""
        if catalog_excerpts is None:
            catalog_excerpts = self.catalog_excerpts(reply_text)
        payload = json.dumps(
            {
                "user_turn": user_text,
                "assistant_reply": reply_text,
                "tool_calls": tool_summary,
                "catalog_excerpts": catalog_excerpts,
            },
            ensure_ascii=False,
        )
        messages = [
            ChatMessage(role="system", content=self.prompt_text),
            ChatMessage(role="user", content=payload),
        ]
        parts: list[str] = []
        last_error: Exception | None = None
        for attempt in range(2):  # one retry on transient provider faults
            try:
                parts = []
                async for event in self._client.stream(
                    messages, tools=None, temperature=JUDGE_TEMPERATURE
                ):
                    if event.type == "token_delta":
                        parts.append(event.text)
                last_error = None
                break
            except GatewayError as exc:
                # A long run must not die on a judge blip: record it honestly
                # (the harness turns JudgeError into a per-turn failure).
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(2.0)
        if last_error is not None:
            raise JudgeError(f"judge provider call failed: {last_error}") from last_error
        reply = "".join(parts).strip()
        if not reply:
            raise JudgeError("judge returned an empty reply")
        parsed = parse_json_object(reply)
        if parsed is None:
            raise JudgeError(f"judge reply is not a JSON object: {reply[:200]!r}")
        return self._verdict_from_payload(parsed, reply)

    def _verdict_from_payload(self, payload: dict, raw_reply: str) -> JudgeVerdict:
        scores: dict[str, float] = {}
        for dimension in JUDGE_WEIGHTS:
            value = payload.get(dimension)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise JudgeError(
                    f"judge reply missing numeric score for {dimension!r}: {raw_reply[:200]!r}"
                )
            if not 1 <= value <= 5:
                raise JudgeError(f"judge score for {dimension!r} out of range: {value}")
            scores[dimension] = float(value)
        clarifying = payload.get("clarifying_questions", 0)
        if isinstance(clarifying, bool) or not isinstance(clarifying, (int, float)):
            clarifying = 0
        rationale = str(payload.get("rationale", "")).strip()
        return JudgeVerdict(
            scores=scores,
            clarifying_questions=int(clarifying),
            rationale=rationale,
        )
