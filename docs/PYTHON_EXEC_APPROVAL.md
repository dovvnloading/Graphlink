# Fresh approval for persistent Python execution

`python.exec` executes Python with the local process's privileges. This
change hardens the approval path; it does not make Python a sandbox or
reverse effects after execution. Interpreter state persists between calls,
so the same source can do something different the next time it runs.

## Reproduced on main

The starting production code was commit
`72eec3cb7871a9b76f10bbd5fa85ed0356c27ae6`. Before changing implementation,
the first 12 cases in `backend/tests/test_python_approval.py` ran against
that code with disposable directories and the real Python subprocess:
11 failed and the existing cancellation check passed. The failures were:

| Harmless fixture | Before the fix | Required result |
| --- | --- | --- |
| Source with padding past character 400 | Approval summary omitted the tail | Complete exact source disclosed |
| Same counter-increment source twice | Second call ran without asking; counter advanced | Fresh prompt; denial leaves counter unchanged |
| Existing Python session/fingerprint grants | Marker files were written without a new prompt | Fresh denial prevents interpreter startup and writes |
| Session approval followed by different code | Later calls reused the grant | Every call prompts; no new Python session grant |
| Arguments changed while approval was pending | Changed code executed | Refuse the changed request and require new approval |
| Oversized/non-string/blank source | Prompt occurred before input rejection; integer source was coerced | Explicit rejection before prompting or execution |
| Source with leading/trailing newlines | Handler stripped the source | Interpreter receives the disclosed source unchanged |

Only approval decisions were scripted for those cases. The registry,
Python handler, interpreter, persistent variables, and fixture file writes
were real. No provider, external service, live credential, or user checkout
was used. The approved first/third counter calls are positive controls:
the first writes `1`, denial leaves `1`, and fresh approval then writes `2`.

## Enforced behavior

The production Python registration always requires a fresh backend prompt,
ignoring both per-run fingerprints and session grants. The registry copies
arguments before disclosure, gives the approval callback a separate copy,
and refuses a changed pending request before starting the interpreter.
Accepted source is never stripped, coerced, or truncated. Requests above
20,000 characters, non-string/blank code, and invisible controls that cannot
be presented faithfully are refused explicitly. Tab, CR and LF remain
supported; unsupported controls can be represented by visible Python escape
sequences inside string literals.

The React panel renders the complete source as text. It has no Python
session-approval option. A per-disclosure `harnessApprovalId` accompanies
Approve/Deny, and the backend matches it to the current pending future.
The earlier run ID alone is insufficient. Replay against a later prompt,
the generic execution-approval route, session approval, and question-answer
routes cannot authorize a pending Python call. An old Python confirmation
also cannot authorize a subsequent unrelated tool. Existing shell policies
and unrelated-tool grant semantics remain in place.

The shared Builder registry can expose the Python tool, but its unbound
context is refused before interpreter startup. Its summary also discloses
the full source. This change does not extend Python execution to Builder.

## Re-run focused checks

Use the repository's locked Python dependencies and CI development tools,
Node version from `web_ui/.nvmrc`, and `npm ci`. From the checkout root:

```sh
python -m pytest -q -n0 backend/tests/test_python_approval.py backend/tests/test_harness_h6.py backend/tests/test_harness.py backend/tests/test_tool_registry.py
python contracts/codegen.py --check
```

From `web_ui/`:

```sh
npm run typecheck
npm run test -- src/app/canvas/HarnessNodeView.test.tsx src/app/canvas/SceneCanvas.test.tsx src/app/canvas/sceneStore.test.ts
```

The additional cases cover real intent registration and dispatch, stale
confirmation IDs, cross-resolver refusal, double-submit after denial, and
the live harness issuing distinct identities for identical source. The
frontend tests verify complete text rendering, HTML treated as text,
identity forwarding and duplicate-click suppression. These focused checks
are not substitutes for the full repository checks, browser integration
tests, or Windows validation; the integrated evidence records those gates.

## Boundary and limits

The backend, frontend and local owner are trusted. A disclosure identity
binds a response to one pending request; it is not proof that a person read
or consented to the source. Arbitrary code with direct access to internal
handlers/futures is outside this approval boundary. Approved Python can
affect files or external systems with the user's privileges; cancellation
after execution begins cannot promise reversal of those effects. No
challenge submission or acceptance of submission terms is part of this work.
