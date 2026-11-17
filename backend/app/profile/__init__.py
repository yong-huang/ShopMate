"""Cross-session profile pipeline (System Design 3.6, PRD FR-6, ADR-007).

Stages and where they live:

- Capture: ``app.db.record_interaction`` (appends to ``profiles.interaction_log``)
- Extraction: ``app.profile.extract`` (session-end LLM pass over the transcript)
- Merge: ``app.profile.merge`` (per-field last-write-wins with source stamps)
- Injection: ``app.profile.render`` (compact profile block for Maya's prompt)
- Control: ``app.api.profile`` (``GET`` / ``DELETE`` ``/api/profile``)

Extraction always runs off the request path; malformed extractions are
discarded and logged, never merged (ADR-007).
"""
