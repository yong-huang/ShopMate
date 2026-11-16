# AGENTS.md

ShopMate — a conversational shopping assistant (chat agent) for a demo e-commerce storefront. All project documentation, tickets, and code comments are written in **English**.

Design docs live in `docs/` and are the source of truth for what gets built:

- [docs/prd.md](docs/prd.md) — product requirements, journeys, v2 roadmap
- [docs/system-design.md](docs/system-design.md) — architecture, agent & tool design, API contracts, ADRs
- [docs/eval-plan.md](docs/eval-plan.md) — golden-set evaluation, metrics & gates

## Agent skills

### Issue tracker

Issues live in **GitHub Issues** (`yong-huang/ShopMate`), managed via the `gh` CLI, with native blocking dependencies. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/` at the repo root (created lazily as decisions land). See `docs/agents/domain.md`.
