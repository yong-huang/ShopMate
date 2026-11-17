"""ShopMate evaluation harness (docs/eval-plan.md, ticket T08).

Layout (Eval Plan §4):

- :mod:`evals.schema` — golden-set scenario schema + strict loader
- ``evals.golden``    — the 32-scenario golden set (one JSON file each)
- :mod:`evals.judge`  — versioned rubric prompt + LLM-as-judge client
- :mod:`evals.runner` — SSE-driven scripted runner + rule-check engine
- :mod:`evals.aggregate` — metric aggregation + §3.3 gate verdict
- :mod:`evals.report` — JSON + HTML report writing under ``evals/reports/``
- :mod:`evals.run`    — ``python -m evals.run`` CLI (one provider run)
- :mod:`evals.compare`— ``python -m evals.compare`` cross-provider matrix

The runner drives the live app (``create_app``) end to end: real agent graph,
tools, guardrail, catalog, SSE wire format, cart REST API — with the chat
model either configured from the environment (live provider run) or scripted
(``--hermetic`` evidence runs).
"""
