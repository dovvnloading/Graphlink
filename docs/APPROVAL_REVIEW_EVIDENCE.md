# Approval and reviewed-file browser evidence

This suite exercises the built React UI against the production backend in a
real Chromium browser. It supplements the [Gitlink safety demo](GITLINK_SAFETY_DEMO.md)
with the browser confirmation and Python approval paths. All repositories,
settings, transcripts, interpreter workspaces, and sentinel files are disposable.
No challenge submission or acceptance of challenge terms is performed.

The dedicated launcher also redirects the knowledge database and its imported
aliases. The chat library's real autosave indexes into this separate store;
passing only `settings_state_file` and `chat_db_path` would leave that index at
the user's default location. All execution scratch constants are redirected as
well. Production autosave and indexing still run against these disposable
locations. Plugin manifests come from the checked-out source and grants use the
disposable settings file; desktop file logging and token-file launchers are not
invoked by this test entry point.

## Boundary and fixtures

The trusted application and its operator review a proposed action before it
runs. Model output is untrusted. The checks cover Python authorization and
reviewed local Gitlink writes, including stale and replayed requests. They do
not establish that arbitrary Python is safe, that a person read the display,
or that a proposal fingerprint proves human consent.

`tests_e2e/run_safety_backend.py` starts `backend.app.create_app` with its normal
capability-token and origin checks enabled. The Playwright config creates a new
ephemeral test token and hands it to the SPA through the normal URL fragment.
It is unrelated to any live account. The backend binds loopback port 8801 and
refuses to reuse a server from another run. The test-only fixture routes are
installed by this entry point; production `create_app` does not install them.

Only the following inputs or observations are substituted:

- Provider initialization/discovery is disabled, and `chat_turn_with_tools`
  returns a fixed sequence of harmless `ToolCall` objects. There is no model,
  remote request, or live credential in this workflow. Token counting uses the
  application's existing character fallback so a cold run cannot download a
  tokenizer encoding.
- Fixed Gitlink proposals stand in for model output. They pass through the real
  `prepare_change_review` and preview generation. The fixture supplies a fresh
  local checkout and the same trust-list entry a native folder choice would
  produce. Setup also skips onboarding and creates a node; those flows are
  outside the claim.
- An observer wraps `atomic_replace_file`, delegates to the original function
  unchanged, and records each actual completed write. It does not simulate or
  suppress writes. Fixture-only actions independently change a file, replace
  a pending proposal, or mutate the original pending Python arguments.

The browser's normal callbacks send real WebSocket messages. Replay tests
observe the actual outgoing SPA frames and send them through a second native,
authenticated browser WebSocket. They do not replace `WebSocket`,
`WsTransport`, the intent validator, dispatcher, approval future, file-baseline
checks, atomic writer, or persistent Python interpreter.

The test bootstrap applies the production OS/runtime environment allowlist,
discarding inherited provider/cloud credentials and every `GIT_*` environment
variable before Git runs. It also preserves a caller-specified POSIX `TMPDIR`.
Each fixture `git init` uses a scrubbed environment and an empty template
directory. No remote, commit, push, or user checkout is needed. The
fixture API accepts only named, fixed scenarios and actions, never arbitrary
source, paths, or file contents.

## Existing coverage inventoried before adding the suite

| Existing test surface | What it already covers | Browser gap addressed here |
| --- | --- | --- |
| `backend/tests/test_gitlink_review.py` | Captured baselines, stale local files, reviewed apply | Rendered byte-change disclosure and real confirmation |
| `backend/tests/test_gitlink_selective.py` | Selection membership, proposal ownership and consumption | Checkboxes and exact selected paths over WebSocket |
| `backend/tests/test_gitlink_crash_recovery.py` | Recovery and conflict preflight, platform-specific cases | No replacement for these tests; continue running them |
| `backend/tests/test_gitlink_safety_demo.py` and `tools/gitlink_safety_demo.py` | Real intent/dispatcher/writer in eleven disposable scenarios | Demo does not drive the browser; this suite does |
| `backend/tests/test_agents.py` Gitlink cases | Dispatcher checks and state transitions | UI-to-dispatcher integration |
| `backend/tests/test_harness*.py` and tool registry tests | Harness, interpreter, scope and approval rules | Full source display and per-call approval transport |
| `GitlinkNodeView.test.tsx` | Cancel, changed confirmation, fingerprint and selection callbacks | Real backend and real file writes, rather than callback spies |
| `HarnessNodeView.test.tsx` | Approval panel interaction | Real approval resolution and interpreter side effects |
| Existing `web_ui/e2e` specs | Boot, settings, plugin node creation, command palette, save/reload | No previous browser approval/review coverage |

## Cases and positive controls

| Case | Required observation |
| --- | --- |
| Cancel | No Apply frame, zero writer calls, all fixture SHA-256 hashes unchanged |
| Selected subset | Exactly selected paths written; unselected independent edit survives |
| Replay/double-submit | Two copies of the consumed Apply frame cause no additional write |
| Replaced proposal | Old confirmation disabled; old fingerprint refused by backend |
| External edit | A stale later target prevents the first selected write |
| Invalid/escaping selection | Real validation/dispatcher refuses it; outside sentinel unchanged |
| Newline-only change | CRLF/LF metadata visible in real UI; approved action writes exact LF bytes |
| Long review and spaced paths | Wheel/keyboard reach a selectable tail without moving the canvas; visibly distinct escaped paths select exactly one original raw path |
| Python full source | Exact 916-character source displayed; wheel and keyboard scroll reveal/select the executable tail without moving the canvas |
| Python fresh authorization | Seeded old session grant cannot bypass three distinct call approvals |
| Persistent Python state | Separately approved identical code changes a counter from `1` to `2` |
| Stale Python approval | First call's captured approval cannot approve the second call; alternate session/answer intents cannot resolve it |
| Python deny/stop | No proposed file appears; hashes remain unchanged |
| Changed pending arguments | Approving the original display cannot execute altered source |

The subset and newline Apply cases, and the first two Python calls, are
positive controls. A suite in which every operation is unconditionally refused
cannot pass. Tests compare file contents as hex and SHA-256 hashes, preserve an
outside sentinel, and count real writer calls where applicable.

## Reproduction commands

Use Python 3.12 and the repository's locked dependencies, then the dev tool
versions in `.github/workflows/ci.yml`. From the repository root:

```sh
python -m pip install -r requirements.txt
git rev-parse HEAD
git status --porcelain
python tools/gitlink_safety_demo.py --json /tmp/gitlink-safety-report.json
python -m pytest -q
python -m compileall -q .
cd web_ui
npm ci
npm run check
npx playwright install chromium
npm run test:e2e
npm run test:e2e:safety
```

The report destination for the standalone demo must not exist. Use a suitable
new temporary path on Windows. The Python executable on `PATH` must be the
environment containing Graphlink's dependencies, because both Playwright
configs launch it directly. Keep generated evidence outside the checkout when
establishing a clean source revision.

For a local diagnostic run where downloading Playwright's pinned browser is
unavailable, `GRAPHLINK_E2E_CHROMIUM_EXECUTABLE` may point to an already installed
Chromium. Record this override and the actual browser version; it is not the
same browser-version gate as CI's default bundled Chromium.

The safety config uses no retries. Its JSON report is
`web_ui/test-results/safety-results.json`. Every passing case attaches
`safety-evidence.json` with source HEAD, working-tree cleanliness, OS, Python,
Git, Node, browser and backend package versions, expected outcome, before/after
file hex and hashes, actual writer observations, and the fixture substitutions.
Screenshots include the full Python disclosure and newline preview. CI runs
both browser suites and uploads `web_ui/test-results/` even after failure.
Failure traces may contain the ephemeral local test token, which is invalid
after server teardown; they contain no live service credentials.

Regenerate evidence on the final clean commit. Record failures and compare the
same commands on unchanged main; a passed focused suite does not mean the full
repository checks passed. Windows-specific behavior still needs the Windows
CI job, and a Linux browser run does not substitute for it.

## Limits

These are the tested approval and reviewed local-file paths, not a Python or
filesystem sandbox. A hostile process with local filesystem access can race
optimistic checks. Multi-file application is not a whole-batch atomic
transaction. The existing recovery demo covers process termination at a
specific boundary, not universal power-loss recovery or reversal of external
side effects. The internal `apply_change_set` helper retains its independent
legacy default; calling it inside the trusted process without required review
is outside this reviewed UI route.
