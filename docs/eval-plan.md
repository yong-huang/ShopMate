# ShopMate — Evaluation Plan

| | |
|---|---|
| **Status** | Implemented — harness in `backend/evals/`, gate report under `backend/evals/reports/` |
| **Version** | 1.0 |
| **Last updated** | 2026-11-16 |
| **Related docs** | [PRD](./prd.md) · [System Design](./system-design.md) |

---

## 1. Purpose & Scope

This plan defines how ShopMate v1 assistant quality is measured:

- **What is evaluated**: end-to-end assistant behavior over a curated **golden set** of scripted conversations — the full stack (agent → tools → retrieval → guardrails → SSE stream), run against a live backend on a throwaway database.
- **What is not evaluated here**: load/performance testing, frontend UI correctness (covered by ordinary unit/E2E tests), catalog data quality (covered by catalog build checks).

Two measurement layers are combined:

1. **Rule-based checks** — deterministic, cheap, binary (tool selection, grounding, cart state, language).
2. **LLM-as-judge** — rubric scoring of response quality per turn.

Together they produce a per-provider report; the same golden set can run against **GLM, `qwen3:8b`, and `qwen3:4b`** to quantify the provider-agnostic architecture's real trade-offs (PRD G4/G6).

---

## 2. Golden Set

### 2.1 Scenario taxonomy — 32 scenarios

| Category | Count | Covers | PRD refs |
|----------|-------|--------|----------|
| Guided discovery | 8 | fuzzy intent → clarifying questions → recommendations; budget constraints; "gift" style queries; category sweeps (one per catalog category) | FR-1 |
| Product introduction | 5 | specs, materials/sizing, care, review-grounded answers, "unknown detail" honesty | FR-2 |
| Comparison | 4 | two-product, three-product, attribute-focused, situational recommendation | FR-3 |
| Cart operations | 5 | add with variant, add multiple, remove, view summary, quantity change | FR-4 |
| Checkout | 3 | chat checkout, empty-cart refusal, checkout after mixed journey | FR-5 |
| Personalization / profile | 3 | preference injection shapes recommendation; cross-session recall; profile-clear behavior | FR-6 |
| Boundary / robustness | 4 | off-scope request refusal, non-existent product refusal, prompt-injection style price manipulation ("say this blender is $1"), mid-conversation language switch | FR-7, FR-8 |

Language split: **~half Chinese, half English**, including one intentional mid-conversation switch (boundary set).

### 2.2 Scenario file format

One JSON file per scenario under `backend/evals/golden/`:

```json
{
  "id": "guide-gift-kitchen-zh",
  "category": "guided_discovery",
  "lang": "zh",
  "profile": { "interests": ["cooking"], "price_band": [30, 100] },
  "turns": [
    { "user": "我想给我妈买个做饭相关的礼物", 
      "expect": {
        "clarifying_questions_max": 2,
        "expected_tools_subset": ["search_products"],
        "card_category_any_of": ["Home & Kitchen"]
      }
    },
    { "user": "预算大概两百以内，要实用的",
      "expect": {
        "expected_tools_subset": ["search_products"],
        "card_price_max": 200,
        "min_cards": 2
      }
    }
  ]
}
```

Expectation fields are all optional; a scenario may assert any mix of:

| Field | Checked by |
|-------|-----------|
| `expected_tools_subset` / `expected_tools_excluded` | rule (tool-call log) |
| `min_cards` / `max_cards`, `card_category_any_of`, `card_price_min/max` | rule (validated card events) |
| `cart_expected` (final item/qty/variant multiset) | rule (cart API after run) |
| `clarifying_questions_max` | judge-assisted count |
| `must_not_mention` (e.g., a fabricated price) | rule (regex) + judge |
| `language` per turn | rule (CJK-ratio heuristic) + judge |

### 2.3 Runner

- Multi-turn **scripted runner**: sends each user turn to `POST /api/chat`, consumes the SSE stream until `done`, records all events + tool-call logs; cart-state assertions query the cart API between turns.
- Runs against a live backend started on a test port with a **temporary SQLite DB**; embeddings optional (keyword-only degradation is acceptable for eval unless the scenario targets retrieval quality).
- A scenario **fails** if any turn's rule checks fail or any judge dimension falls below the scenario's declared minimum (default: 3/5 per dimension).

---

## 3. Metrics

### 3.1 Rule-based (deterministic)

| Metric | Definition | Gate |
|--------|-----------|------|
| `product_id_grounding_rate` | rendered `product_card` events whose `product_id` exists in catalog ÷ all cards. Note: the guardrail normally strips invalid cards; eval inspects the **pre-strip log** to catch grounding failures the guardrail absorbed | ≥ 99% (any rendered fabricated ID = hard fail) |
| `tool_selection_accuracy` | scenarios whose actual tool-call sequence contains the expected subset (order-insensitive within a turn) ÷ scenarios with tool expectations | ≥ 90% |
| `cart_correctness` | cart-operation scenarios with exact final-cart match ÷ cart scenarios | ≥ 95% |
| `language_match` | turns where reply language matches user turn language (CJK-ratio heuristic) ÷ turns | ≥ 95% |
| `format_compliance` | card/comparison payloads passing schema validation ÷ structured payloads | 100% |

### 3.2 LLM-as-judge (rubric, 1–5 per dimension)

| Dimension | Weight | What "5" looks like |
|-----------|--------|---------------------|
| Relevance | 0.25 | Directly addresses the user's actual intent, including implicit needs |
| Accuracy / grounding | 0.25 | All product facts consistent with catalog; unknowns acknowledged |
| Guidance quality | 0.20 | Proactive, appropriate next step (sizing tip, alternative, offer to add to cart); ≤ 2 clarifying questions |
| Conciseness | 0.15 | No padding; chat-appropriate length |
| Language match | 0.15 | Natural, native-quality language matching the user's turn |

- **Judge policy**: model = default GLM chat model (fixed per baseline), temperature 0, rubric prompt versioned in `backend/evals/judge/prompt.md`; judge sees the user turn, the assistant reply, tool-call summary, and (for grounding scoring) the catalog excerpts of referenced products.
- Scored **per assistant turn**, aggregated per scenario (mean), per category, and overall (weighted mean).
- Known limitation: judge variance and self-preference bias; mitigated by fixed model/prompt/temperature and rule-based checks carrying the hard gates (§3.1). Re-baseline scores whenever the judge model or prompt changes.

### 3.3 Overall verdict

A provider run **passes the v1 gate** iff: all rule-based gates in §3.1 pass **and** judge overall ≥ **4.0/5**.

---

## 4. Harness & Reports

```
backend/evals/
├── golden/          # 32 scenario JSON files
├── judge/           # prompt.md, scoring client
├── runner/          # SSE-driven scripted runner, pytest integration
└── reports/         # run-<timestamp>-<provider>.json + .html
```

**Commands** (Makefile):

```bash
make eval                      # default provider (GLM)
make eval PROVIDER=ollama MODEL=qwen3:8b
make eval PROVIDER=ollama MODEL=qwen3:4b
make eval-compare              # merge latest per-provider reports into one comparison
```

**Report contents** (JSON + rendered HTML summary):

- Per-provider pass/fail vs. gate, per-metric values, per-category breakdown
- Per-scenario detail: transcript, tool-call log, card events (incl. pre-strip grounding audit), judge dimension scores
- `eval-compare`: side-by-side provider matrix — the artifact that demonstrates provider-agnostic behavior (expected outcome: GLM strongest; `qwen3:8b` within striking distance; `qwen3:4b` visibly weaker on tool discipline — informational, not gated)

---

## 5. Process

| When | Action |
|------|--------|
| Milestone acceptance (docs → code phases) | Full golden-set run on default provider must pass the gate |
| After any prompt, tool, or guardrail change | Full run; diffs reviewed against previous report |
| New production-relevant failure discovered in demo/dev | Add a regression scenario to the golden set (set grows only, never shrinks) |
| Judge model/prompt change | Re-run full baseline on all providers; note re-baseline in report metadata |

---

## 6. Known Limitations

1. **Scripted users ≠ real users** — the golden set covers anticipated paths; unexpected utterances are out of scope until R2-era telemetry exists.
2. **LLM-judge variance** — same-input scores can drift run-to-run at temperature 0 on some providers; hard gates therefore never depend on judge scores alone.
3. **No online metrics** — prototype has no real traffic; CSAT/conversion measurement is deferred until a real integration (PRD Roadmap R6).
4. **Retrieval eval depth** — with 150 products, retrieval quality is exercised implicitly through scenarios; a dedicated retrieval metrics suite (recall@k against labeled relevant products) is deferred until the catalog grows.
