# Workspace agent: Scientific Workbench tools

The workspace agent can discover and execute operations belonging to the user's
**enabled** Scientific Workbench packs. This uses the existing file-discovered
workspace tool catalog; there is no separate pack registry or agent prompt list.

## Agent workflow

1. Enable the desired `science_<pack>` plugin in **Settings > Plugins**.
2. Ask the workspace agent to perform a calculation. It can call
   `science.search` with no arguments to list enabled packs, or supply
   `pack_id` and/or `query` to find operations. Matching operations include
   their ID, description, input fields and example arguments. Results are paged
   using `offset` and `limit`.
3. The agent calls `science.run` with `pack_id`, `operation_id`, and
   `inputs`, after the usual execution/mutation approval.
4. The Workbench validates inputs and computes the result in its existing
   isolated, bounded, cancellable worker. The handler verifies the result's
   receipt before creating anything in the graph.
5. On success, an undoable, run-attributed `science_<pack>.workbench` node
   appears to the right of and connected to the workspace agent. The tool
   result includes `node_id`, `values`, `summary`, and the complete
   `receipt` (`provenance`, including hashes and input units). It becomes a
   tool message in the agent's transcript, so the agent can explain the
   calculation using the actual values instead of inventing an answer.

Example calls for the Mathematics pack:

```json
{"name": "science.search", "arguments": {"pack_id": "mathematics", "query": "integrate"}}
{"name": "science.run", "arguments": {"pack_id": "mathematics", "operation_id": "trapezoid", "inputs": {"x": [0, 0.5, 1, 2], "y": [1, 2, 3, 5]}}}
```

## Boundaries

- Discovery reads Settings grants **at call time**. Disabled packs are not
  imported or exposed in operation results. Enabled packs must also be
  activated (with a registered Workbench serializer).
- `science.search` is read-only. `science.run` has `code.execute` and
  `graph.mutate` scopes and requires the regular tool approval.
- Invalid inputs, timeout, cancellation, receipt damage, grant revocation,
  agent deletion or workspace switch **do not create a result node**.
- Tool calls accept literal, self-contained operation inputs. The workspace
  agent does not implicitly execute connected graph sources or accept
  `sources` bindings; for connected-data pipelines, use the Workbench's
  manual bindings and Graph Runner.
- As with a manual Workbench node, the result persists through the plugin's
  normal serializer and is checked against its receipt when read again.
