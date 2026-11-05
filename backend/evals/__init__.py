"""ADR-016 stage 16.5: local evals harness (goldens + optional judge) for the
agent/chart/structured-output paths.

Offline-by-default and covered by CI through test_evals_harness.py. Three dimensions:

- **chart**: does ``respond_json`` (ADR-013 stage 13.3) + ``canonicalize_chart_data``
  turn a scripted model response into the exact canonical chart payload
  (backend/api/intents_chart.py's own downstream shape)? Real, working code.
- **structured_output**: does ``backend.structured_output.respond_json`` parse
  a scripted model response into the exact expected dict for a given JSON
  Schema? Real, working code.
- **agent_build**: real ``backend.builder.run_build`` with scripted model turns,
  real graph tools, approval gates, state/budgets, and graph-output scoring.
  No network/code-execution tools are registered in these deterministic tests.

Run it: ``python -m backend.evals``. The ``--live`` option uses a real
provider for chart/structured-output fixtures; Builder stays offline and
scripted for safety. See backend/evals/README.md.
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
