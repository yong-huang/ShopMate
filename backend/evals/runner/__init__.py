"""SSE-driven scripted runner (Eval Plan §2.3) + rule checks (§3.1).

- :mod:`evals.runner.capture` — turn-log capture (``chat_turn`` records:
  tool hops with ok flags, token usage, the guardrail validation record incl.
  the PRE-strip failing card ids).
- :mod:`evals.runner.session` — the per-scenario HTTP surface: cookie-jarred
  ``POST /api/chat`` SSE turns + cart/profile/session REST calls.
- :mod:`evals.runner.harness` — the live-app fixture (``create_app`` on a
  throwaway SQLite DB), scenario seeding (profile rows / seed cart), and
  :func:`run_scenario`.
- :mod:`evals.runner.fake_model` — the hermetic scripted chat model + judge
  used for evidence runs (``python -m evals.run --hermetic``) and tests.

The runner drives the real app end to end — agent graph, tools, guardrail,
catalog, SSE wire format — so a golden-set run measures the shipped stack.
"""
