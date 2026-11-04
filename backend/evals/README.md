# Local evals harness (ADR-016 stage 16.5)

A local eval harness: golden fixtures for chart generation, structured
output, and the real Builder execution loop. Its offline mode is deterministic
and runs in CI through `backend/tests/test_evals_harness.py`. Charts and
structured output can also be exercised against a configured live provider.

## The three dimensions

- **chart** - drives the real `backend.structured_output.respond_json` call
  against `graphlink_chart_data.CHART_JSON_SCHEMAS`/`chart_generation_messages`
  (the exact schema+messages `backend/agents.py`'s `_call_chart_agent` uses -
  ADR-013 stage 13.3 retired the old `ChartDataAgent` pipeline this dimension
  used to drive) and canonicalizes the result the same way
  `backend/api/intents_chart.py`'s `generate_chart` does downstream, via
  `graphlink_chart_data.canonicalize_chart_data`.
- **structured_output** - drives the real `backend.structured_output.respond_json`
  against a JSON Schema + scripted model response.
- **agent_build** - executes the actual `backend.builder.run_build`
  coroutine against a real `SceneDocument`, `ToolRegistry`, and
  `RunRegistry`. Only the model's tool-call turns are scripted. It checks
  node and edge output, completed steps, approvals, tool outcomes, budgets,
  and transitions across resume. The eval registry has **no network, code
  execution, provider or MCP tools**, so it stays offline even with `--live`.

## Running it

``bash
python -m backend.evals            # fully scripted, deterministic, offline
python -m backend.evals --live     # live chart/structured-output model calls only
python -m pytest -q backend/tests/test_evals_harness.py
```

The CLI exits nonzero if any fixture fails. In default mode chart and
structured-output calls patch `api_provider.chat`; Builder calls patch
`api_provider.chat_turn_with_tools` while exercising the real Builder
coroutine and graph tools. No configured provider, API key, or network is
needed. `--live` does not change Builder scoring, since live agent execution
would involve permission and side-effect risks. There is no LLM-as-judge;
all dimensions compare observed results against exact expectations.

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
- `scripted_response` contains the raw text returned by chart or
  structured-output models. Builder fixtures set it to `null` and use
  scripted tool turns inside `input.runs` instead. All shipped fixtures
  require an explicit `expected` object.

### chart fixtures

`input` needs `chart_type` (`"bar"` / `"line"` / `"pie"` / `"histogram"` /
`"sankey"`) and `source_text` (what a user's node history would contain).
`scripted_response` is the exact raw JSON text a model would emit for that
`chart_type`, matching `graphlink_chart_data.CHART_JSON_SCHEMAS[chart_type]`.

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

Builder input declares a goal, mode, ordered step titles, and one or more
execution runs. Each run has its own ordered model turns, optional approval
answers, and optional budget overrides (`max_steps`, `max_tokens`,
`max_wall_seconds`). A second run executes the same plan node, preserving
spent budgets and completed steps, so a pause/resume test is real.

```json
{
  "name": "example_note",
  "kind": "agent_build",
  "input": {
    "goal": "Write a note",
    "mode": "autopilot",
    "steps": ["Create the note"],
    "runs": [{
      "turns": [{
        "usage": {"prompt_tokens": 20, "completion_tokens": 5},
        "tool_calls": [
          {"id": "create", "name": "graph.create_node",
           "arguments": {"kind": "note", "content": "Hello"}},
          {"id": "done", "name": "builder.complete_step",
           "arguments": {"summary": "Note written"}}
        ]
      }]
    }]
  },
  "scripted_response": null,
  "expected": {
    "status": "done", "run_statuses": ["done"],
    "steps": [{"title": "Create the note", "status": "done", "detail": "Note written"}],
    "nodes": [{"kind": "note", "content": "Hello"}],
    "edges": [],
    "activity": [
      {"tool": "graph.create_node", "outcome": "ok"},
      {"tool": "builder.complete_step", "outcome": "ok"}
    ],
    "approvals": [], "spent_steps": 1, "spent_tokens": 25
  }
}
```

`expected` must include all nine scoring fields above. Node and edge lists
are order-independent; activity, approval prompts and step progress are
order-sensitive. To additionally check the final banner, set
`status_detail_contains`. For a co-pilot run, specify `"approvals": [true,
false]` in the order approvals are requested, and assert the names of those
requests in `expected.approvals`.

For tools that need an earlier created node's id, use a string like
`"$node:create"` in a later turn's arguments. The eval provider resolves
it from the *actual* `graph.create_node` tool response, so fixture steps
cannot depend on a guessed node id. A reference in the same turn as its
create call is invalid. Running out of scripted turns, leaving turns unused,
or requesting a tool outside the offline allowlist fails the evaluation.

These are **execution-state and graph-output tests**, not an assessment of
a live model's ability to reason. The scripted model controls tool choice;
the real Builder owns all mutations, approvals, budgets and checkpoints.

## Adding a fixture

1. Pick the right `fixtures/<kind>/` directory.
2. Write a new `*.json` file following the shape above - any filename
   works, fixtures are loaded in filename-sorted order.
3. For a chart fixture, derive `expected` by actually running
   `canonicalize_chart_data` (see above) - never hand-write it.
4. Run `python -m backend.evals` and confirm your new fixture shows `PASS`.
5. For Builder fixtures, include all required expected graph/state fields
   and one or more fully scripted runs. The shipped fixture suite is CI-gated.
