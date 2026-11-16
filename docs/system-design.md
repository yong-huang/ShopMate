# ShopMate — System Design

| | |
|---|---|
| **Status** | Draft for review (design phase — implementation not started) |
| **Version** | 1.0 |
| **Last updated** | 2026-11-16 |
| **Related docs** | [PRD](./prd.md) · [Evaluation Plan](./eval-plan.md) |

---

## 1. Overview

ShopMate is a conversational shopping assistant (persona: **Maya**) embedded in a demo storefront. This document specifies the architecture, agent design, tool & API contracts, data model, and deployment topology for v1, plus the architecture decision records (ADRs) behind the key choices.

### Design principles

| # | Principle | Consequence |
|---|-----------|-------------|
| P1 | **Platform-agnostic core** — all commerce data flows through adapter interfaces | `CatalogAdapter` / `OrderAdapter` contracts defined now; real platforms plug in later (PRD R6) |
| P2 | **Provider-agnostic model layer** — one OpenAI-compatible gateway | GLM, OpenAI, Ollama switchable by env only |
| P3 | **Grounded by validation, not hope** — product rendering is structurally constrained and post-validated | No fabricated products/prices reach the UI |
| P4 | **Streaming-first UX** — tokens, tool progress, and structured cards stream over SSE | Perceived latency stays low even on local models |
| P5 | **Boring, inspectable data** — JSON catalog + SQLite | Zero-ops demo; trivially editable mock data; migration path to Postgres |

---

## 2. High-Level Architecture

```
┌────────────────────────────── Browser ────────────────────────────────┐
│  Next.js App Router · Tailwind · shadcn/ui                            │
│  ┌──────────────┐ ┌───────────────────┐ ┌──────────────────────────┐  │
│  │ Storefront    │ │ Chat widget (Maya)│ │ Preferences panel        │  │
│  │ home/product/ │ │ SSE client, cards │ │ profile view / clear     │  │
│  │ cart/checkout │ │ tool progress     │ │                          │  │
│  └──────┬───────┘ └────────┬──────────┘ └────────────┬─────────────┘  │
└─────────┼──────────────────┼─────────────────────────┼────────────────┘
          │ REST             │ SSE (stream)            │ REST
┌─────────▼──────────────────▼─────────────────────────▼────────────────┐
│                     FastAPI backend  (port 8000)                      │
│                                                                       │
│  ┌─────────── API layer ───────────┐   ┌────────────────────────────┐ │
│  │ /api/chat (SSE) · /api/products │   │ Model Gateway              │ │
│  │ /api/cart · /api/checkout       │──►│ OpenAI-compatible facade   │ │
│  │ /api/profile · /api/sessions    │   │ ┌─────┐ ┌──────┐ ┌───────┐ │ │
│  └───────────────┬─────────────────┘   │ │ GLM │ │OpenAI│ │Ollama │ │ │
│                  │                     │ └─────┘ └──────┘ └───────┘ │ │
│                  ▼                     └────────────▲───────────────┘ │
│  ┌───────────────────────────────────────┐          │                 │
│  │ LangGraph agent — Maya (single agent) │          │                 │
│  │   chatbot ──► tools ──► validate ──►  │──────────┘                 │
│  │   (state machine · SqliteSaver)       │  chat completions + tools  │
│  └──────┬──────────────────────┬─────────┘                            │
│         │                      │                                      │
│  ┌──────▼───────┐   ┌──────────▼──────────────┐   ┌─────────────────┐│
│  │ Tool layer   │   │ Retrieval (hybrid)      │   │ Guardrails /    ││
│  │ 7 LLM tools  │   │ structured + BM25 +     │   │ card validation ││
│  │ catalog/cart │   │ vector (bge-m3), RRF    │   │ retry / strip   ││
│  └──────┬───────┘   └──────────┬──────────────┘   └─────────────────┘│
│         │                      │ in-memory index                     │
│  ┌──────▼──────────────────────▼───────────────┐   ┌─────────────────┐│
│  │ Catalog adapter  │ Profile pipeline         │   │ SQLite          ││
│  │ (products.json)  │ (async session-end       │   │ (aiosqlite /    ││
│  │                  │  extraction + merge)     │   │  SQLAlchemy)    ││
│  └──────────────────┴──────────────────────────┘   └─────────────────┘│
└───────────────────────────────────────────────────────────────────────┘
        Embeddings: Ollama bge-m3 on host (fallback: GLM embedding → none)
        Ollama access from containers: http://host.docker.internal:11434
```

### Component responsibilities

| Component | Responsibility |
|-----------|----------------|
| **Frontend (Next.js)** | Storefront pages, chat widget (SSE client, product/comparison cards, tool progress), cart UI, checkout UI, preferences panel |
| **API layer (FastAPI)** | REST endpoints + the one SSE endpoint; session/cookie management; request validation |
| **LangGraph agent** | Maya's brain: single ReAct-style agent — chatbot ⇄ tools ⇄ validate loop, checkpointer-backed per-session state |
| **Model gateway** | Uniform chat-completions + tool-calling interface over GLM / OpenAI / Ollama (OpenAI-compatible protocols) |
| **Tool layer** | 7 LLM-callable tools over catalog, cart, and checkout |
| **Retrieval** | Hybrid product search: structured filters + keyword (BM25) + optional vector (bge-m3), fused with RRF |
| **Guardrails** | Post-generation validation of product cards (existence, price integrity); retry/strip policy |
| **Catalog adapter** | Loads `products.json`; the single seam for future real-commerce integration |
| **Profile pipeline** | Async session-end preference extraction, schema-validated merge, context injection |
| **Storage** | SQLite for sessions, messages, carts, orders, profiles; JSON files for the catalog |

---

## 3. Component Design

### 3.1 Model Gateway

A single interface hides the provider:

```python
class ChatClient(Protocol):
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.3,
    ) -> AsyncIterator[ChatEvent]:  # token-delta | tool_call | finish
        ...
```

- All providers are addressed through their **OpenAI-compatible** chat-completions + function-calling surface (GLM and Ollama both expose one), so a single HTTP client serves all three.
- Provider presets (env-driven, see §3.9):

| Preset | `LLM_PROVIDER` | Base URL | Default model |
|--------|----------------|----------|---------------|
| Cloud (default) | `glm` | GLM OpenAI-compatible endpoint (configurable) | `glm-5.3-flash` |
| Local | `ollama` | `http://localhost:11434/v1` (containers: `host.docker.internal`) | `qwen3:8b` |
| Cloud (alt) | `openai` | OpenAI API | configurable |

- **Degradation**: if the configured local model does not emit valid tool calls, the agent detects the failure pattern (malformed/absent tool JSON after N attempts) and returns an honest "this model can't run the shop tools — try `qwen3:8b` or switch provider" message instead of hallucinating.

### 3.2 Agent — LangGraph State Machine

**Topology (v1: single agent, split-ready):**

```
START → chatbot ──no tool calls──► validate ──ok──► END
            │  ▲                      │
            │  └── tool results ──────┘ (loop)
            └──has tool calls──► tools ──┘
```

- **State schema** (per session thread):

```python
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]  # conversation
    tool_results: list[ToolResult]     # last hop's tool outputs
    pending_cards: list[ProductCard]   # cards awaiting validation
    cart_events: list[CartEvent]       # surfaced to SSE as cart_update
```

- **Nodes**:
  - `chatbot` — LLM call with the tool set and the Maya system prompt (persona, grounding rules, clarifying-question policy, bilingual mirroring); context block includes known profile (§3.6), cart summary, and the catalog category list.
  - `tools` — executes requested tool calls (bounded: max 6 tool hops per turn).
  - `validate` — runs the guardrail pass (§3.5) over `pending_cards` before anything is streamed as a card.
- **Checkpointer**: `SqliteSaver` — one thread per session, so conversation state survives restarts; raw messages are additionally persisted to the `messages` table for logs/eval.
- **Split-readiness for v2 (R3)**: nodes are pure functions over state; tools are grouped (catalog/cart/checkout) so a router + specialists refactor does not touch the API layer.

**System prompt (Maya)** — maintained in `backend/app/agent/prompts/maya.md`, compiled per turn with: persona & rules, profile block, cart summary, catalog category/tag overview. Key rules:

1. Mirror the user's language every turn.
2. Recommend **only** products returned by `search_products`/`get_product_details`; render them as product-card blocks with `product_id`.
3. Ask at most 2 clarifying questions; then recommend.
4. Never state a price or spec not present in tool output.
5. Off-scope requests (non-shopping): decline politely, offer shopping help.

### 3.3 Tool Specification

Seven LLM-callable tools. Schemas are OpenAI function-calling JSON Schema.

| Tool | Purpose | Key inputs | Returns |
|------|---------|------------|---------|
| `search_products` | Hybrid catalog search | `query`, `filters{category?, price_min?, price_max?, tags?, attributes?}`, `limit≤8` | product summaries: id, name_en/zh, price, rating, one-line pitch, match hint |
| `get_product_details` | Full record for one product | `product_id` | full fields: specs, materials, sizing, care, reviews, stock |
| `compare_products` | 2–3 product comparison | `product_ids[2..3]` | normalized attribute matrix + per-product strengths |
| `add_to_cart` | Add item (with optional variant) | `product_id`, `quantity=1`, `variant?{size?, color?}` | cart summary + validation errors (unknown variant, out of demo stock) |
| `get_cart` | Current cart contents | — | items (product, qty, variant, line price), cart total |
| `remove_from_cart` | Remove a cart line | `cart_item_id` | cart summary |
| `checkout` | Demo order placement | `shipping_note?` | order id, summary, demo-disclaimer text |

Notes:

- Tool outputs are **the only source** of product data the model may present (P3). Card payloads are attached to `pending_cards` by the tool layer at execution time, so validation checks what the tools actually returned — not what the model claims.
- `checkout` writes an `orders` row (status `demo_paid`) and clears the cart; it refuses when the cart is empty.
- All tools are async; catalog tools hit the in-memory index, cart/checkout tools hit SQLite.

### 3.4 Retrieval — Hybrid Search

**Index build (startup / `make catalog` reload):** each product becomes one document: bilingual name + description + tags + category + key specs. Two structures in process memory:

1. **Keyword index** — BM25 (rank-bm25) over tokenized docs (zh tokens via jieba, en via whitespace+lowercase).
2. **Vector index** — embeddings cached on disk (`.embeddings/`) keyed by content hash; cosine similarity over a numpy matrix.

**Query path** (inside `search_products`):

1. Apply structured filters (category/price/tags/attributes) → candidate set.
2. Score candidates: BM25 score and (if embeddings available) cosine score; **fuse with Reciprocal Rank Fusion** (k=60).
3. If the vector backend is unavailable, fall back to BM25-only silently.
4. Return top `limit` summaries + `match_hint` (why this product fits the query) for the model to reason over.

**Embedding provider chain** (`EMBEDDING_PROVIDER=auto`):

```
auto → try ollama (EMBEDDING_MODEL=bge-m3) → try glm embedding API → keyword-only
```

For ~150 products the entire index is tiny (< a few MB) — no external vector store (ADR-003). Index reload is exposed as a management command, not a runtime API (prototype scope).

### 3.5 Guardrails — Post-Generation Validation

Structured product cards are the **only** rendering path for products:

1. When the model wants to show products, it emits card blocks referencing `product_id`s; the frontend renders cards **only** from validated `product_card` SSE events, never from model prose.
2. **Validator checks** per card: (a) `product_id` exists in catalog; (b) price/rating on the card equal catalog values; (c) card payload matches the SSE card schema.
3. **Failure policy**: retry the `chatbot` node once with a corrective system message ("card X referenced a non-catalog product; use tool results only"); if still invalid → strip invalid cards, stream the text with no card, log a `validation_failure` for the eval harness.
4. **Explicitly not validated**: free-text prose mentioning prices (too brittle to parse reliably); grounding there is enforced by prompt rules + eval measurement instead (documented tradeoff).

### 3.6 Profile Pipeline

| Stage | Behavior |
|-------|----------|
| **Capture (real-time)** | Interaction events (product viewed, added to cart, purchased) append to the user's interaction log in SQLite |
| **Extraction (async, session end)** | Triggered by: 15-min idle timeout, explicit new session, or browser unload ping. An LLM pass over the session transcript produces a **schema-validated** preference object: `{interests[], price_band?, sizes{}, likes[], dislikes[], freeform_notes[]}` |
| **Merge** | Per-field last-write-wins with source-session timestamp; conflicts resolved toward the newer session; schema validation failure ⇒ discard that extraction (log it), never corrupt the profile |
| **Injection** | Next session: profile serialized compactly into Maya's context block; capped (~300 tokens) |
| **Control** | `GET /api/profile` renders the panel; `DELETE /api/profile` clears it (interaction log included); nothing is re-injected after clear |

Extraction runs in a FastAPI background task; the chat request path never waits on it. Extraction uses the same model gateway (default provider), so it works identically on GLM and Ollama.

### 3.7 API Specification

All endpoints under `/api`. Auth: none; anonymous identity via `shopmate_sid` HttpOnly cookie (issued on first request; maps to `users.id`).

**Chat (the only streaming endpoint):**

```
POST /api/chat
body: { "session_id": "uuid", "message": "..." }
→ SSE stream, event types:
   session       {session_id, user_id}
   token         {text}                        # assistant text delta
   tool_call     {name, args_summary}          # progress indicator
   product_card  {product_id, name_en, name_zh, price, currency, rating,
                  image_url, match_hint}       # validated payload only
   comparison    {product_ids[2..3], matrix, recommendation}
   cart_update   {items, total}                # after cart-affecting tools
   done          {message_id}
   error         {code, message}               # validation|model|internal
```

**Storefront support endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/products?category=&q=&sort=` | catalog browsing (storefront list) |
| GET | `/api/products/{id}` | product detail page data |
| GET | `/api/cart` | cart page/drawer data |
| POST | `/api/cart/items` | storefront "Add to cart" button (non-chat path) `{product_id, quantity, variant?}` |
| PATCH | `/api/cart/items/{cart_item_id}` | update quantity |
| DELETE | `/api/cart/items/{cart_item_id}` | remove line |
| POST | `/api/checkout` | demo checkout from cart page |
| GET | `/api/profile` | preferences panel data |
| DELETE | `/api/profile` | clear profile |
| POST | `/api/sessions` | start a new session (triggers prior-session profile extraction) |
| GET | `/api/health` | liveness + provider/index status |

Errors: JSON `{code, message}`; `4xx` for client errors, `5xx` with `error` SSE event for stream failures. All endpoints documented again in code as OpenAPI schema (FastAPI default).

### 3.8 Data Model

**SQLite (via SQLAlchemy async + aiosqlite)** — tables:

```sql
users     (id PK, created_at)                    -- anonymous, cookie-linked
sessions  (id PK, user_id FK, started_at, ended_at, lang)
messages  (id PK, session_id FK, role, content, tool_calls JSON,
           created_at)                           -- logs + eval source
carts     (user_id PK, updated_at)
cart_items(id PK, cart_id FK, product_id, quantity, variant JSON)
orders    (id PK, user_id FK, total, currency, status, created_at)  -- status: demo_paid
order_items(id PK, order_id FK, product_id, name_snapshot, unit_price, quantity, variant JSON)
profiles  (user_id PK, interests JSON, price_band, sizes JSON,
           likes JSON, dislikes JSON, notes JSON, interaction_log JSON,
           updated_at)
```

**Catalog (`backend/data/catalog/products.json`)** — 150 products:

| Field | Notes |
|-------|-------|
| `id`, `slug` | stable identifiers (`SKU`-style ids) |
| `name_en` / `name_zh`, `description_en` / `description_zh` | bilingual display fields |
| `category` | one of: Electronics (30) · Home & Kitchen (40) · Apparel & Accessories (40) · Sports & Outdoors (40) |
| `price` / `currency` | USD default |
| `attributes`, `specs` | structured, comparison-friendly |
| `sizing?`, `care?`, `materials?` | apparel-heavy fields that exercise FR-2 |
| `reviews[]`, `rating`, `stock` | Top-30 hand-curated reviews; templated elsewhere |
| `tags[]` | retrieval hints |
| `image` | generated SVG placeholder path |

**Images**: deterministic SVG placeholders generated at catalog build (`make catalog`) — category emoji + stable color derived from product id; no external image dependencies.

### 3.9 Configuration

All behavior switches are env vars (`.env`; `backend/.env.example` is the source of truth):

| Variable | Default | Notes |
|----------|---------|-------|
| `LLM_PROVIDER` | `glm` | `glm` \| `openai` \| `ollama` |
| `LLM_MODEL` | `glm-5.3-flash` | any provider model id |
| `LLM_BASE_URL` / `LLM_API_KEY` | — / — | provider endpoint & key |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | also used for embeddings |
| `OLLAMA_CHAT_MODEL` | `qwen3:8b` | used when `LLM_PROVIDER=ollama` |
| `EMBEDDING_PROVIDER` | `auto` | `auto` \| `ollama` \| `glm` \| `none` |
| `EMBEDDING_MODEL` | `bge-m3` | Ollama tag; GLM embedding id when glm |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/shopmate.db` | |
| `SESSION_IDLE_TTL_MIN` | `15` | profile-extraction trigger |
| `BACKEND_PORT` / `FRONTEND_PORT` | `8000` / `3000` | |

Presets: **GLM mode** = defaults (only `LLM_API_KEY` needed); **Ollama mode** = `LLM_PROVIDER=ollama` (no key, embeddings included).

### 3.10 Deployment

- **Development**: `make dev` → uvicorn (reload) + Next dev server, concurrently.
- **Demo**: `docker compose up` → services `api` (backend image) + `web` (Next build), shared volume `./backend/data`; Ollama stays on the host, reached via `host.docker.internal:11434`. GLM mode requires only `LLM_API_KEY` in `.env`.

---

## 4. Key Data Flows

**F1 — Chat turn**

1. Frontend `POST /api/chat` (SSE) with session + message → API resolves cookie → user/session.
2. LangGraph thread restored (checkpointer); profile + cart summary injected.
3. `chatbot` → streams `token`s; on tool calls → `tool_call` progress → `tools` node executes → `validate` checks pending cards → loop or finish.
4. SSE emits `token` / `tool_call` / `product_card` / `cart_update` / `done`; messages persisted.

**F2 — Checkout (either surface)**

`POST /api/checkout` or `checkout` tool → validate cart non-empty → create `orders` + `order_items` (snapshot names/prices) → clear cart → return order id → confirmation modal → success page.

**F3 — Profile extraction**

Session end trigger → background task loads transcript → LLM extraction (JSON-schema enforced) → merge into `profiles` → next session's context block includes it.

---

## 5. Repository Layout (planned)

```
ShopMate/
├── README.md · Makefile · docker-compose.yml · .env.example
├── docs/                     # prd.md · system-design.md · eval-plan.md
├── backend/
│   ├── pyproject.toml        # uv-managed
│   ├── app/
│   │   ├── main.py · config.py
│   │   ├── api/              # routes: chat, products, cart, checkout, profile, sessions
│   │   ├── agent/            # graph.py, state.py, prompts/maya.md
│   │   ├── tools/            # 7 tools + schemas
│   │   ├── retrieval/        # index.py, bm25.py, vector.py, fusion.py
│   │   ├── gateway/          # model gateway + provider adapters
│   │   ├── catalog/          # adapter.py, loader.py
│   │   ├── guardrails/       # validators.py
│   │   ├── profile/          # extract.py, merge.py
│   │   ├── db/               # models.py, session.py
│   │   └── sse/              # event types + encoder
│   ├── data/
│   │   ├── catalog/products.json + images/ (generated SVGs)
│   │   └── shopmate.db       # runtime
│   └── evals/                # golden/*.json, judge/, runner/, reports/
└── frontend/                 # Next.js app router
    ├── app/                  # page.tsx (home), product/[id], cart, checkout/success
    ├── components/           # chat/ (widget, cards, progress), storefront/, profile/
    └── lib/                  # sse client, api client
```

---

## 6. Testing Strategy

| Layer | Approach |
|-------|----------|
| Tools & retrieval | Unit tests with the mock catalog (deterministic) |
| Guardrails | Unit tests for validator + retry/strip policy |
| API | `httpx` + ASGI tests: cart flows, SSE event shapes, session cookie |
| Agent | Smoke tests on graph execution (mocked gateway) |
| End-to-end behavior | Golden-set evaluation — see [Evaluation Plan](./eval-plan.md) |

---

## 7. Architecture Decision Records

**ADR-001 — Provider-agnostic gateway via OpenAI-compatible interface.**
Context: GLM is the default, but Ollama must be first-class and others may follow. Decision: one `ChatClient` facade over OpenAI-compatible chat-completions + tool-calling for all providers. Consequence: single client to maintain; provider quirks (e.g., local model tool reliability) handled at the gateway/agent edge; no vendor SDK lock-in.

**ADR-002 — Single agent + tools in v1 (not multi-agent).**
Context: four capabilities, moderate complexity. Decision: one ReAct-style LangGraph agent; nodes/tools grouped for a clean v2 split into router + specialists. Consequence: simplest system that can work; avoids router misclassification risk in v1; explicit refactor path (R3).

**ADR-003 — Hybrid retrieval with in-process index; no external vector DB.**
Context: ~150 products, bilingual queries, prototype. Decision: BM25 + optional bge-m3 vectors fused by RRF, all in process memory; embeddings optional with graceful degradation to keyword-only. Consequence: zero-ops, fast, portable; would be replaced by a real vector store only at catalog scales where recall measurably degrades.

**ADR-004 — SQLite over Postgres for the prototype.**
Decision: SQLite (aiosqlite) for all dynamic state. Consequence: zero-config demo and Docker volume simplicity; SQLAlchemy keeps the Postgres migration a config change.

**ADR-005 — SSE over WebSocket for chat streaming.**
Decision: unidirectional server→client stream fits the interaction; HTTP stays simple (no sticky sessions, easy proxying). Consequence: no bi-channel (if v2 needs server-initiated pushes outside a request, revisit).

**ADR-006 — Post-generation validation as the grounding mechanism.**
Decision: structural card rendering + existence/price validation with retry-then-strip, instead of trusting prompt constraints alone. Consequence: strong guarantee on rendered commerce data; free-text grounding remains prompt+eval-enforced (documented tradeoff).

**ADR-007 — Session-end async profile extraction over per-turn extraction.**
Decision: batch extraction at session end (idle TTL / explicit new session). Consequence: cheaper, more coherent preference summaries; tradeoff: profile updates land one session late — acceptable for v1, revisit with R5 (proactive recommendations).

**ADR-008 — Mock catalog as JSON with adapter seam.**
Decision: `products.json` + generated SVG images behind a `CatalogAdapter` interface. Consequence: hand-editable demo data and a stable contract for real-platform integration (R6); no database coupling in the agent core.
