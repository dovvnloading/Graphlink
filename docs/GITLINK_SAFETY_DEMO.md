# Reviewed Gitlink edits: reproducible safety demo

Graphlink's reviewed Gitlink Apply route binds a file-change proposal to
server-held contents and the original file bytes. It refuses mismatched or
stale requests, applies only selected paths, and can restore an interrupted
batch on the next Gitlink access. This demo exercises those mechanisms using
real files and the production backend, with no model or external service.

## Run it

Requirements: Git on PATH, Python 3.12, and the repository's locked Python
dependencies. From a Graphlink checkout with the environment activated:

```sh
python -m pip install -r requirements.txt
python tools/gitlink_safety_demo.py --json gitlink-safety-report.json
```

The expected summary is `11/11 checks passed`. Each case gets a fresh temporary
Git repository. All fixture files and recovery journals are removed afterward,
including when a case fails. There is no option to target a real user checkout.
No Git commits, remote requests, model calls, credentials, or desktop window
are needed. The JSON report is the only persistent output; its destination
must not already exist, so use a new filename for each run.

The report records source HEAD, whether the working tree was dirty, Python,
OS, and each case's observed outcome. It exits nonzero if any check fails.
Run from a clean checkout of the reviewed commit for attributable evidence.
Do not treat a report's source HEAD as representing uncommitted modifications.

For automated regression coverage (install the dev tools listed in CI first):

```sh
python -m pytest -q -n0 backend/tests/test_gitlink_safety_demo.py backend/tests/test_gitlink_review.py backend/tests/test_gitlink_selective.py backend/tests/test_gitlink_crash_recovery.py
```

The standalone demo is also run by `test_gitlink_safety_demo.py`, so it cannot
quietly drift into a separate implementation of the safety logic.

## What the cases prove

| Case | Observed invariant |
| --- | --- |
| Missing fingerprint | Request refused; zero calls to the atomic writer; fixture file hashes unchanged. |
| Tampered server-held proposal | Old fingerprint rejected after proposed content changes; zero writes. |
| Changed checkout | The same proposal cannot be redirected to another checkout; both remain unchanged. |
| Unknown selection | A requested path not in the server-held proposal is rejected; zero writes. |
| Stale last target | A later target changed by an editor stops the whole selected set before the first write. |
| Missing baselines | A restored legacy proposal with a matching fingerprint but no original-byte hashes cannot write. |
| Selected subset and replay | Exactly two selected paths change; an unselected editor change survives; the whole proposal is consumed and replay writes nothing. |
| Path escape | A `../` path is rejected during review; an outside sentinel is unchanged. |
| Process loss | A child process exits abruptly after the first real atomic replacement; next repository read restores original bytes and requires re-review. |
| Independent edit after crash | Recovery refuses before restoring any files and retains the journal for manual review. |
| Directory after crash | A directory replacing a formerly absent file is not treated as missing; recovery leaves files and directory untouched and retains the journal. |

The intent cases use `SessionBus.dispatch_intent` with the real
`applyGitlinkChanges` registration, `AgentDispatcher`, node state transitions,
review validation, journal, and writer. Only the proposal is a deterministic
fixture instead of an LLM response. A wrapping observer counts real atomic
writes without substituting their behavior. Negative cases compare SHA-256
snapshots of all fixture files, not just a return value or notification.

For process loss, a separate Python process runs the real reviewed writer. A
test-only wrapper calls `os._exit(73)` immediately after the first successful
atomic replacement. This skips Python exception rollback and cleanup. The
parent verifies the actual mixed file state and durable manifest, then calls
the real repository read path to recover. This is process termination at one
defined boundary, not a power-loss or every-crash-boundary test.

The recovery preflight regression tests additionally cover both directory and
POSIX FIFO replacements of create/delete targets. Before the accompanying
fix, recovery could restore another file before failing on a directory, or
unlink an independently created FIFO. The fix rejects nonregular targets
during the complete preflight, before restoration begins.

## Scope and limits

- This is a focused deterministic test of the reviewed desktop/backend Apply
  route, not a safety rate measured against live models or a prompt-injection
  benchmark. No claim is made that generated code is safe or correct.
- A proposal fingerprint checks content consistency. It is not a secret,
  signature, authentication credential, or cryptographic proof of human
  approval. The trusted UI has a separate confirmation interaction. Its
  existing `GitlinkNodeView.test.tsx` tests cover Cancel, stale confirmation,
  and forwarding the exact fingerprint/selection. The demo does not drive a
  browser, prove the user read the diff, or test the WebSocket auth boundary.
- The backend/UI and local checkout owner are trusted. Arbitrary callers of
  the internal `apply_change_set` helper can explicitly use its legacy
  `require_review=False` default. That helper is not a security boundary for
  hostile code running inside Graphlink's process.
- Baseline hashes and UTF-8 review previews use the same captured bytes.
  Newline style, final-newline and UTF-8 BOM changes are explicit; non-UTF-8
  originals are refused. Pre-upgrade previews require regeneration. See
  [Byte-faithful reviewed previews](GITLINK_BYTE_PREVIEWS.md) for the supported
  encoding boundary, reproduced failure, and focused regression commands.
- These are file-content safeguards, not a sandbox, filesystem lock, or
  multi-file atomic transaction. Readers can observe a partially applied
  batch. An independent process can still race a validation check and write.
  Recovery cannot promise to undo arbitrary external side effects.
- Recovery preserves unknown edits rather than overwriting them. When it
  refuses, inspect the files and retained sibling journal manually; do not
  delete the journal or overwrite independent work just to clear the error.
- The Linux demonstration does not validate Windows DACL behavior. The
  platform-specific tests in `test_gitlink_crash_recovery.py` require Windows.
  A passing demo is not a substitute for the repository's complete CI checks.

## Evidence map

- Intent and checkout/fingerprint binding: `backend/api/intents_gitlink.py`,
  `backend/agent_dispatch/gitlink.py`.
- Baselines and server-owned selection: `graphlink_plugins/gitlink/change_review.py`.
- Atomic file writes and recovery: `graphlink_plugins/gitlink/repository.py`,
  `graphlink_plugins/gitlink/apply_transaction.py`.
- Proposal consumption: `backend/domain/nodes_gitlink.py`.
- Existing detailed regressions: `backend/tests/test_gitlink_review.py`,
  `test_gitlink_selective.py`, `test_gitlink_crash_recovery.py`, plus Gitlink
  Apply cases in `test_agents.py`.
- Existing UI confirmation tests: `web_ui/src/app/canvas/GitlinkNodeView.test.tsx`.

For a short demonstration, show the selected-subset case, stale-file refusal,
and crash recovery, then show the limitations above. Describe the new work as
reproducible integration evidence and a focused recovery fix, not as newly
inventing all of the existing safeguards. This document and script do not
submit or publish a challenge entry.
