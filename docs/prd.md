# ShopMate — Product Requirements Document

| | |
|---|---|
| **Status** | Implemented (v1) — see GitHub issues #1–#10 and the eval gate report |
| **Version** | 1.0 |
| **Last updated** | 2026-11-16 |
| **Related docs** | [System Design](./system-design.md) · [Evaluation Plan](./eval-plan.md) |

---

## 1. Overview

### 1.1 Problem

Shoppers on e-commerce sites face choice overload. Keyword search and static filters cannot capture fuzzy intent ("a gift for my mom who loves cooking"), and product pages answer questions only if the shopper already knows what to ask. Human shopping assistants provide guidance but do not scale.

A conversational agent embedded in the storefront can carry a shopper from **fuzzy intent → concrete candidates → confident decision → purchase** inside a single chat surface.

### 1.2 Product

**ShopMate** is a conversational shopping assistant named **Maya**, embedded in a demo storefront. Maya guides shoppers with clarifying questions and personalized recommendations, answers in-depth questions about specific products, produces side-by-side comparisons, and lets users add items to the cart and check out — without leaving the conversation.

### 1.3 Nature of this release

ShopMate v1 is a **working prototype**: a complete, polished shopping-assistant experience running on a self-contained mock catalog. The agent core is **platform-agnostic** — all commerce data flows through Catalog/Order adapter interfaces — so the assistant can later be wired to a real commerce backend without redesign.

### 1.4 Goals

| ID | Goal |
|----|------|
| G1 | Demonstrate credible **guided selling**: multi-turn dialogue, clarifying questions, personalized recommendations |
| G2 | Guarantee **catalog grounding**: Maya never invents products, prices, or specs |
| G3 | **Bilingual conversation** (English / Chinese); Maya mirrors the user's language |
| G4 | **Provider-agnostic model layer**: GLM by default, Ollama (local models) as a first-class option |
| G5 | **One-command demo**: `docker compose up` or `make dev` |
| G6 | **Measurable quality**: golden-set evaluation harness with rule-based checks and LLM-as-judge scoring |

### 1.5 Non-goals (v1)

- No real payments, user accounts, or inventory management
- No production-scale or high-availability concerns
- No order-status lookup or after-sales flows (v2 — see Roadmap)
- No human-handoff escalation (v2)
- No admin console or merchant-facing configuration UI

---

## 2. Users & Personas

| Persona | Description | Needs |
|---------|-------------|-------|
| **Shopper** (primary) | A visitor browsing the demo storefront, in English or Chinese | Find the right product quickly; get trustworthy answers; buy with minimal friction |
| **Store Operator** (evaluator) | A stakeholder assessing whether this assistant is worth integrating into a real store | See the full journey work end-to-end; inspect quality metrics |
| **Developer** (integrator) | An engineer who will connect the agent core to a real commerce platform | Clean adapter interfaces; documented API/tool contract; provider flexibility |

---

## 3. Product Experience

### 3.1 Surfaces

- **Storefront** (Next.js): home page (categories, featured products), product detail pages, cart page, demo checkout (confirmation + success), preferences panel
- **Chat widget**: persistent on every storefront page; expandable to a full-page chat mode
- **Preferences panel**: lightweight view of the user profile Maya has learned, with a one-click clear

### 3.2 Key Journeys

| ID | Journey | Expected behavior |
|----|---------|-------------------|
| J1 | **Cold-start greeting** | Shopper opens the widget; Maya greets briefly, offers help categories, does not block browsing |
| J2 | **Fuzzy-intent guidance** | "I need a gift for my mom who loves cooking" → Maya asks ≤ 2 clarifying questions (budget, preferences), then recommends 3–4 product cards with reasons |
| J3 | **Deep product Q&A** | "Is this jacket machine-washable? What sizes?" → Maya answers strictly from catalog data (materials, sizing chart, care, reviews) for a specific product |
| J4 | **Comparison** | "Compare these two blenders" → Maya returns a side-by-side comparison card (price, key specs, ratings) and a situational recommendation |
| J5 | **Chat add-to-cart** | "Add the blue one, size M" → Maya adds via tool, confirms with a cart summary; cart visible in storefront |
| J6 | **Checkout** | Via chat ("I'm done, check out") or cart page → order confirmation modal → success page with order number; order record persisted (demo, no payment) |
| J7 | **Returning user** | Session with prior profile → Maya's context includes known preferences; greeting may reference last-seen interests ("Welcome back — last time you looked at camping lights") |
| J8 | **Boundary** | Non-shopping requests (coding help, medical advice) or requests for products not in the catalog → Maya politely declines/clarifies; never fabricates a product |

### 3.3 Maya — Persona & Conversational Rules

- Friendly, knowledgeable shopping consultant; concise by default
- Asks **at most 2 clarifying questions** before recommending; never interrogates
- Products are always rendered as **structured product cards** (image, name, price, rating, match reason) — never as free-text listings of invented items
- Mirrors the user's language turn-by-turn (including mixed en/zh)
- States uncertainty honestly ("I don't have that detail in the catalog")

---

## 4. Functional Requirements (v1)

### FR-1 Shopping Guide (guided selling)
Maya understands shopping intent expressed loosely, asks clarifying questions (budget, occasion, recipient, preferences), and recommends products from the catalog with a match reason for each.
- **AC1.1** Given a fuzzy intent query, Maya produces a recommendation set of 2–4 products retrieved via the catalog search tool, each rendered as a product card.
- **AC1.2** Given insufficient constraints, Maya asks ≤ 2 clarifying questions before recommending.
- **AC1.3** Every recommended product exists in the catalog (validated — see FR-8).

### FR-2 Product Introduction
Maya answers detailed questions about a specific product: specs, materials, sizing, care instructions, what reviewers say.
- **AC2.1** Given a product reference (card click, product page, or chat mention), Maya retrieves full product details via tool before answering.
- **AC2.2** Answers are consistent with catalog fields; unknown details are acknowledged, not invented.

### FR-3 Comparison
Maya compares 2–3 candidate products across price, key specs, and rating, then offers a situational recommendation ("choose A if …, B if …").
- **AC3.1** Comparison covers only catalog-verified attributes.
- **AC3.2** Comparison is rendered as a structured comparison card.

### FR-4 Cart Operations in Chat
Maya adds items to the cart, reports cart contents, and removes items, on request.
- **AC4.1** "Add X" results in a cart-state change confirmed by a cart summary; the storefront cart reflects it.
- **AC4.2** Cart operations are idempotent per explicit request (no phantom duplicates).
- **AC4.3** Quantity/variant requests (size, color) are honored when the catalog defines variants.

### FR-5 Demo Checkout
A fake checkout flow closes the loop: confirmation modal → success page with order number; the order is persisted.
- **AC5.1** Checkout via chat or cart page produces a persisted order record and a success page.
- **AC5.2** No real payment UI is implied; copy clearly labels the flow as a demo.

### FR-6 Cross-Session User Profile
ShopMate maintains an anonymous, cookie-linked profile per user.
- **AC6.1** After a session ends (idle timeout or explicit new session), preferences are extracted by the LLM and merged into the profile (async, schema-validated).
- **AC6.2** Profile content (interests, price band, sizes, likes/dislikes, interaction history) is injected into Maya's context in subsequent sessions.
- **AC6.3** The preferences panel displays the profile and offers one-click clearing; cleared data is not re-injected.
- **AC6.4** No personal identifying information is requested or stored.

### FR-7 Bilingual Conversation
- **AC7.1** Maya replies in the language of each user turn (en / zh), including mid-conversation switches.
- **AC7.2** Catalog display names and descriptions are bilingual (en/zh); other catalog fields may be English-only with values understandable in context.

### FR-8 Catalog Grounding (guardrails)
- **AC8.1** All product cards must reference a product ID that exists in the catalog; validation runs post-generation (see [System Design §3.5](./system-design.md)).
- **AC8.2** Prices and ratings shown on cards are taken from catalog data, not generated.
- **AC8.3** On validation failure, the system retries once, then strips the invalid card and continues; the user is never shown a fabricated product.

### FR-9 Streaming Chat UX
- **AC9.1** Assistant responses stream token-by-token (SSE).
- **AC9.2** Tool activity is surfaced as lightweight progress indicators ("searching the catalog…").
- **AC9.3** Product cards and cart confirmations render inline within the stream.

---

## 5. Non-Functional Requirements

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-1 | Responsiveness | First token < 2.5 s on GLM provider; streaming throughout. Local models may be slower — progress indicators required |
| NFR-2 | Languages | UI copy and catalog display fields bilingual (en/zh) |
| NFR-3 | Model portability | Switching provider/model requires **env config only** — no code changes |
| NFR-4 | Observability | Structured per-turn logs: tool calls, latencies, token usage, validation outcomes |
| NFR-5 | Deployment | `make dev` for development; `docker compose up` for demo |
| NFR-6 | Privacy | Anonymous session cookie only; profile clearable; no PII collected |

---

## 6. Roadmap

v2 features are planned, prioritized, and dependency-mapped. Ordering reflects recommended implementation sequence.

| # | Item | Priority | Depends on | Notes |
|---|------|----------|------------|-------|
| R1 | **Order status & after-sales Q&A** (order lookup tool, return/exchange policy flows) | P0 | Order API adapter; orders persisted by v1 checkout | Highest user value; v1 already writes order records, so the data exists |
| R2 | **Human handoff escalation** | P0 | — | Detection heuristics (repeated failure, explicit request, sensitive topics) + support-queue stub |
| R3 | **Multi-agent topology** (router + specialist agents) | P1 | Growing tool/capability count from R1–R2 | v1 ships as a single agent designed for clean splitting (see [System Design §3.2](./system-design.md)) |
| R4 | **Multimodal search** (photo → product, voice queries) | P1 | Provider abstraction | Local `qwen3-vl` (already available) is the natural Ollama-side model; GLM vision API on the cloud side |
| R5 | **Proactive recommendations** (return visits, promotions, restock nudges) | P2 | Profile (v1 FR-6) | Requires UX design review to avoid pushiness |
| R6 | **Real platform adapters** (Shopify, custom commerce APIs) | P2 | Stable adapter interfaces | The Catalog/Order adapter contracts are defined in v1 precisely to enable this |

---

## 7. Success Metrics (prototype stage)

| Metric | Target | Measured by |
|--------|--------|-------------|
| Product-ID grounding rate | ≥ 99% (zero fabricated IDs rendered) | Rule-based eval — [Eval Plan](./eval-plan.md) |
| Tool-selection accuracy | ≥ 90% | Rule-based eval |
| Cart-operation correctness | ≥ 95% | Rule-based eval |
| Language match | ≥ 95% | Rule heuristic + judge |
| LLM-judge overall score | ≥ 4.0 / 5 on default provider | LLM-as-judge |
| Demo journey | J1–J8 all pass in a live walkthrough | Manual acceptance |

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Local models (e.g., `qwen3:4b`) produce weaker tool calls | Default local model is `qwen3:8b`; prompts hardened for tool discipline; eval comparison quantifies the gap; UI shows graceful degradation messages |
| LLM latency on cloud providers | Streaming + tool-progress indicators keep perceived latency low |
| Bilingual profile extraction quality | Judge dimension "language match"; curated zh cases in the golden set |
| Mock catalog feels artificial | Top 30 products hand-curated (rich specs, reviews, bilingual copy); remaining 120 template-generated with quality floor |
| Judge score variance (LLM-as-judge) | Fixed judge model & prompt, temperature 0; known limitation documented in Eval Plan |

---

## 9. Open Questions

| # | Question | Owner |
|---|----------|-------|
| 1 | Which real commerce platform should the first production adapter target — Shopify or a custom commerce API? | Store operator |
| 2 | Should the mock catalog support configurable currency (USD default) for regional demos? | Product |
| 3 | For R2 handoff: live-chat vendor vs. simple in-app queue for the prototype? | Product |
