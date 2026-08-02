"""The scene domain package (ADR-002 stage 2.2).

Pure graph domain code only: the scene data model, its invariants, and the
content codec. Nothing under this package may import backend infrastructure
(FastAPI, backend.events, backend.notifications, backend.agents, ...) or
touch a session bus - enforced by tests/test_domain_purity.py, in the same
permanent-gate style as tests/test_no_qt_anywhere.py.

backend/canvas.py remains the orchestration/wire/registration layer and
imports these names for its own use; external consumers keep importing from
backend.canvas unchanged (those re-exports are real usage by canvas's own
closures, not a compatibility wrapper).
"""
