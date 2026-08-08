# Local evals harness (ADR-016 stage 16.5)

A lightweight, local eval harness: golden fixtures + optional live-provider
run for the agent/chart/structured-output paths (see
`doc/adr/ADR-016-observability-and-cost.md`, section "4. Evals harness").
Kept off-CI on purpose: the default mode is deterministic and CI-safe (see
`backend/tests/test_evals_harness.py`), but the point of the harness is to
be reproducible locally with either a scripted response or a real model.

## The three dimensions

- **chart** - drives the real `ChartDataAgent.get_response` (in
  `graphlink_chart_agent.py`) and canonicalizes the result the same way
  `backend/api/intents_chart.py`'s `generate_chart` does downstream, via
  `graphlink_chart_data.canonicalize_chart_data`.
- **structured_output** - drives the real `backend.structured_output.respond_json`
  against a JSON Schema + scripted model response.
- **agent_build** - ADR-008's Builder agent tool-use loop does not exist as
  shipped code yet (design-doc only - see
  `doc/adr/ADR-008-agentic-graph-construction.md`, stages 8.1-8.6). This
  dimension always reports `status="not_implemented"`, on every fixture,
  and never raises - it exists as honest scaffolding for when the loop
  actually ships.

## Running it

```
python -m backend.evals            # default: scripted responses, deterministic, no network
python -m backend.evals --live     # real, already-configured provider
```

Default mode patches `api_provider.chat` with `unittest.mock.patch.object`
so it never touches a real provider or the network - safe to run anytime,
on any machine, with no configuration. It exits nonzero if any fixture's
`status == "failed"`. `status == "not_implemented"` (the whole agent_build
dimension, today) is informational and never fails the exit code.

`--live` skips the patch and calls straight through to whatever provider is
actually configured for this machine (a real API key, or a running Ollama/
llama.cpp instance) - this is the "optional LLM-judge" mode the ADR
mentions; there is no separate judging model wired in beyond that
straight call-through, and a live run's result depends on that day's real
model output, so it is not expected to be as stable as the default mode.

The same fixtures also run under `pytest` in default (patched) mode - see
`backend/tests/test_evals_harness.py`, part of the normal CI suite.

## Fixture shape

Each fixture is one JSON file under `backend/evals/fixtures/<kind>/`, where
`<kind>` is `chart`, `structured_output`, or `agent_build`:

```json
{
  "name": "unique_fixture_name",
  "kind": "chart",
  "input": { "...": "whatever the scorer for this kind needs" },
  "scripted_response": "the exact raw text a model would emit",
  "expected": { "...": "the exact value a passing run should produce" }
}
```

- `name` is optional (falls back to the filename minus `.json`).
- `scripted_response` and `expected` may both be `null` (used by the
  `agent_build` placeholder fixture, since that dimension never reads
  either field).

### chart fixtures

`input` needs `chart_type` (`"bar"` / `"line"` / `"pie"` / `"histogram"` /
`"sankey"`) and `source_text` (what a user's node history would contain).
`scripted_response` is the exact raw JSON text a model would emit for that
`chart_type`, matching the `STRUCTURE` documented in
`ChartDataAgent.CHART_PROMPTS[chart_type]` in `graphlink_chart_agent.py`.

`expected` must be the **actual** output of
`canonicalize_chart_data(json.loads(scripted_response), chart_type)` - do
not hand-write it. Some chart types add defaulted fields that are easy to
miss by inspection alone (for example, `canonicalize_chart_data` adds
`xAxis`/`yAxis` defaults for `pie` charts even though pie's own model-facing
prompt never asks for them) - derive `expected` by actually running the
function:

```python
import json
from graphlink_chart_data import canonicalize_chart_data

data = json.loads(scripted_response)
print(json.dumps(canonicalize_chart_data(data, chart_type), indent=2))
```

### structured_output fixtures

`input` needs `schema` (a JSON Schema dict, in the subset
`backend/structured_output.py` validates - `type`/`properties`/`required`/
`items`/`enum`), `messages` (the chat messages list), and optionally `task`
(defaults to `"task_chat"`) and `schema_name` (defaults to `"response"`).
`scripted_response` is the exact raw text a model would emit;  `expected` is
the parsed dict `respond_json` should return.

### agent_build fixtures

`input` can be anything descriptive (for example `{"goal": "..."}`) - the
scorer never inspects it. These fixtures exist to name the builds this
dimension will eventually cover, not to be scored today.

## Adding a fixture

1. Pick the right `fixtures/<kind>/` directory.
2. Write a new `*.json` file following the shape above - any filename
   works, fixtures are loaded in filename-sorted order.
3. For a chart fixture, derive `expected` by actually running
   `canonicalize_chart_data` (see above) - never hand-write it.
4. Run `python -m backend.evals` and confirm your new fixture shows `PASS`.
5. If it should be part of CI, add or extend a case in
   `backend/tests/test_evals_harness.py`.
