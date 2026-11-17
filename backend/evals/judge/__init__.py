"""LLM-as-judge scoring (Eval Plan §3.2).

- ``prompt.md`` — the versioned rubric prompt (5 weighted dimensions); the
  version is parsed from its header and stamped into every report so judge
  changes are traceable and re-baselining is explicit.
- :class:`evals.judge.client.Judge` — scores one assistant turn at
  temperature 0 given the user turn, the reply, the tool-call summary, and
  catalog excerpts of every referenced product.
"""
