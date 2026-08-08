"""ADR-016 stage 16.5: local evals harness (goldens + optional judge) for the
agent/chart/structured-output paths.

Local-only, reproducible, off-CI by default. Three dimensions:

- **chart**: does ``ChartDataAgent.get_response`` + ``canonicalize_chart_data``
  turn a scripted model response into the exact canonical chart payload
  (backend/api/intents_chart.py's own downstream shape)? Real, working code.
- **structured_output**: does ``backend.structured_output.respond_json`` parse
  a scripted model response into the exact expected dict for a given JSON
  Schema? Real, working code.
- **agent_build**: ADR-008's Builder agent tool-use loop (plan -> propose
  tool call -> execute -> observe -> checkpoint) is design-doc text only
  today - no ``create_node``/``run_node`` tool, no loop, no checkpoints ship
  yet (see doc/adr/ADR-008-agentic-graph-construction.md, stages 8.1-8.6, all
  pending). This dimension is honest scaffolding: it always reports
  ``not_implemented``, never a fake pass and never an exception.

Run it: ``python -m backend.evals`` (add ``--live`` to hit a real,
already-configured provider instead of a scripted/patched response - see
backend/evals/README.md).
"""

from backend.evals.fixtures import EvalFixture, load_fixtures
from backend.evals.runner import (
    EvalResult,
    run_agent_build_fixture,
    run_all,
    run_chart_fixture,
    run_structured_output_fixture,
)

__all__ = [
    "EvalFixture",
    "load_fixtures",
    "EvalResult",
    "run_agent_build_fixture",
    "run_all",
    "run_chart_fixture",
    "run_structured_output_fixture",
]
