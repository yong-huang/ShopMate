# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root (if it exists).
- **`docs/adr/`**: read ADRs that touch the area you're about to work in. Note: v1 ADRs (ADR-001 … ADR-008) currently live inside [docs/system-design.md](../system-design.md) §7; promote them to `docs/adr/` files when a decision is first revisited.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill creates them lazily when terms or decisions actually get resolved.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `GLOSSARY.md`. Key v1 terms (from the design docs): **Maya** (assistant persona), **catalog adapter**, **product card**, **grounding**, **golden set**, **profile extraction**.

## Flag ADR conflicts

If your output contradicts an existing ADR (see [docs/system-design.md](../system-design.md) §7), surface it explicitly rather than silently overriding:

> _Contricts ADR-002 (single agent), but worth reopening because…_
