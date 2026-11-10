# Safety case: Python approval and reviewed local-file changes

This work strengthens two application paths: authorization of each
`python.exec` request and review of local Gitlink file changes. The starting
reference and checked main were both
`72eec3cb7871a9b76f10bbd5fa85ed0356c27ae6` (PR #537). Implementation, independent
review, and browser evidence use separate branches/worktrees. This document
does not submit a challenge entry or accept submission terms.

## Threat boundary

The operator, Graphlink backend, and application UI are trusted. Model-authored
source, file contents, and paths are untrusted proposals. A delayed UI message,
changed pending request, stale file baseline, or repeated confirmation must not
silently authorize a different action. Tests use fixed harmless proposals,
disposable repositories, and an ephemeral local capability token. They do not
measure a live model's safety or prove that a human read the display.

Python runs with the application's local privileges in a persistent interpreter.
An approval is permission for one disclosed invocation; it is not a Python
sandbox. A proposal fingerprint establishes consistency, not human consent.
Hostile processes with filesystem access and arbitrary callers inside the
trusted backend process are outside these boundaries.

## Reproduced failures and fixes

| Before the change | Fix and observable positive control |
| --- | --- |
| Python's generic approval summary stopped after 400 characters. | The complete source is rendered as inert text. Source over 20,000 characters, non-string/blank input, and unsupported invisible controls are explicitly refused. No executable source is silently truncated or stripped. |
| An identical Python call could reuse its fingerprint grant; a session grant could cover different later source. | The production Python registration forces a fresh backend prompt every time, regardless of either cache. Separately approved identical calls advance a persistent counter from 1 to 2; denial leaves it unchanged. |
| Mutable pending arguments could differ from what was disclosed. | Separate execution and disclosure snapshots are compared with the original request before execution. Changed arguments are refused and need a new request/approval. |
| A run request ID was reused across prompts. | Each Python prompt has a new approval identity. Replayed identities and generic/session/question-answer approval routes cannot settle a different Python prompt. |
| CRLF/LF conversions, final-newline changes, and mixed-ending reordering could say “No textual diff available” despite different byte hashes. | Review uses the exact bytes captured for the baseline. Sizes, hashes, line-ending counts, final-newline state, BOM state, and reversible escaped line content visibly identify the change. An approved CRLF-to-LF action writes exactly the disclosed LF bytes. |
| Legacy decoding could conceal conversion from non-UTF-8 bytes; a BOM could appear only as an invisible character. | UTF-8 and UTF-8 BOM are supported explicitly. Other encodings and malformed Unicode proposals are refused without conversion or writes. Saved proposals made with the old preview format require regeneration. |
| The canvas could intercept wheel and pointer gestures in long review panes. | Python source and Gitlink review reserve their own scrolling and selection gestures and provide keyboard focus. Browser cases check visible tail geometry, wheel/Home/End behavior, pointer selection, and an unchanged canvas transform. |
| Filenames with one or two spaces could look identical in selection and confirmation labels. | Paths with spaces or other ambiguous characters use an injective quoted display, with each ASCII space shown as literal `\u0020`. The actual selected path remains unchanged; a positive control writes only the one-space filename. |

The initial Python regression run against unchanged production code produced
11 failures and one passing cancellation control. The byte probe used real
preparation, preview generation, reviewed validation, and writes; its report
records original/proposed bytes and hashes. Focused details and reproduction
examples are in [Python approval](PYTHON_EXEC_APPROVAL.md) and
[byte previews](GITLINK_BYTE_PREVIEWS.md).

Existing proposal/checkout binding, selected-subset checks, stale-file checks,
proposal consumption/replay protection, and recovery remain in place. The
internal `apply_change_set` helper retains its `require_review=False` default;
this work does not globally change its call-site contract.

## Evidence and independent review

The [browser evidence suite](APPROVAL_REVIEW_EVIDENCE.md) drives the built React
UI through real authenticated WebSocket requests, intent validation,
`AgentDispatcher`, reviewed-file validation, the atomic writer, and the real
persistent interpreter. Only fixed model inputs, native-folder setup, optional
token estimation, provider discovery, and a delegating write observer are
substituted. Cancel, subset application, replaced proposals, stale baselines,
replay, newline disclosure, Python approval/denial/Stop, and changed pending
source are exercised. Successful selected writes and separately approved Python
calls prevent a refuse-everything implementation from passing.

Independent review added real concurrent-Apply/replay and post-preview symlink
probes. It also found a test-fixture isolation defect: autosave could reach the
default knowledge database outside the disposable fixture. A safe reproduction
redirected that default to a disposable sibling database and observed the real
autosave write. The dedicated test bootstrap now redirects the knowledge store
and its captured aliases, as well as execution scratch roots. A regression
forces real autosave and checks that the outside sentinel database is untouched.
This was a test-environment repair, not a production filesystem redesign.
The reviewer also identified the review-pane gesture and filename-display
issues above. Component tests cover their wiring; the browser scenarios test
actual scrolling, selection, and exact-path delivery to the writer.

Each browser case attaches the source commit, Git cleanliness, OS and tool
versions, explicit substitutions, expected/actual outcome, file hex and SHA-256
snapshots, and completed-write observations. CI retains screenshots and failure
traces. Final evidence must be generated from the final clean commit; an earlier
passing run is not evidence for later edits.

## Commands and release gates

Use the locked Python requirements and exact development-tool versions in
`.github/workflows/ci.yml`. Build before running Python tests in the same
checkout: Vite replaces its output directory, which some backend fixtures read.

```sh
git rev-parse HEAD
git status --porcelain
cd web_ui
npm ci
npm run check
cd ..
python -m compileall -q .
python -m ruff check .
python -m mypy
python -m pytest -q --cov --cov-report=term-missing --ignore=backend/tests/perf
python -m pytest -q -n0 backend/tests/perf
python tools/gitlink_safety_demo.py --json /absolute/new/path/gitlink-report.json
cd web_ui
npx playwright install chromium
npm run test:e2e
npm run test:e2e:safety
```

The report path must be new and outside the checkout. Use a writable temporary
directory and the intended Python environment on `PATH`. The demo and browser
bootstrap scrub inherited Git routing variables before fixture Git commands.
Record any browser executable override. Windows DPAPI/recovery tests need the
Windows CI job; `mypy --platform win32` on Linux is only a static check.

Local baseline comparison is required when a gate fails. In this execution
environment unchanged main had 22 Python failures and 23 skips on Linux, plus
13 native-Linux mypy errors involving Windows-specific APIs. Local Chromium
could not start because socket creation was denied. These limitations must
remain visible alongside hosted CI outcomes; passing focused tests must not be
reported as a complete local pass. The draft PRs and final evidence manifest
record exact heads, commands, results, and any remaining gate failures.

## Limits and release decision

There is no protection here against a hostile local process racing optimistic
filesystem checks. Readers may observe a partially applied multi-file batch.
The existing recovery demo tests process termination at a defined boundary,
not all power-loss scenarios or reversal of external side effects. Preview
support is deliberately limited to byte-faithful UTF-8; unsupported content is
refused. These are application authorization and reviewed-file safeguards,
not general execution confinement.

Keep the work in draft until independent review and the applicable CI gates
are assessed. Merging, publishing a challenge entry, and agreeing to its terms
remain separate decisions. The stated November 15 deadline does not authorize
any of those actions.
