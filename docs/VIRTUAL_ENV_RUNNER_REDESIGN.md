# Virtual Environment Runner — audit and upgrade plan

Status: code-level audit, Nov 2026. Initial safety/UX increment in this PR.
The plugin manifest in `plugins/code_sandbox` is only registration. Production
runs traverse `CodeSandboxNodeView` / `sceneStore`, the scene intent
`runCodeSandbox`, `CodeSandboxDispatchOps`, and
`graphlink_plugins.code_sandbox.domain.VirtualEnvSandbox`.

## Security boundary: dependency isolation is NOT OS isolation

A Python virtual environment isolates installed packages. Python code in it
still runs as the same OS user as Graphlink, with access to that user's files
and (unless separately restricted by the OS/network) network connections.
Each run requires a human code/manifest approval, and source distributions
require an additional explicit source-build opt-in. The resource guard limits
some processes/memory but **is not a strong security containment boundary**.
Never market this as a secure sandbox for fully adversarial Python. Production
hard isolation would require a dedicated sandbox process with restricted
OS-level credentials, filesystem mounts, network policy and a robust kernel
security boundary; validate on Windows/macOS/Linux independently.

## High-severity reliability findings

1. **Unbounded stdout/stderr capture** — `_run_subprocess` previously
   used a limitless `queue.Queue()`, `readline()` without a size,
   and a list that appended every line. A child that printed indefinitely,
   or a giant line without a newline, could exhaust Graphlink memory despite
   the subprocess's ordinary timeout and job/process limits. The UI also
   appended all live stream chunks in memory. Fix in this increment: bounded
   chunk reads, bounded queue/backpressure, explicit output quota that stops
   the subprocess, and a bounded terminal *view* retaining the newest output.
   This is a fail-closed process stop, not silent data truncation. Successful
   output below the quota preserves its bytes/text and line streaming.

2. **Overlapping subprocesses on one object orphan process tracking** —
   existing unit tests explicitly recorded that a new `_run_subprocess`
   call overwrote `self.guard` and `self.current_process` while the
   first child was still running. Today dispatch creates one sandbox object
   per run, but the domain class did not enforce the invariant. Use a
   nonblocking instance lock and refuse any overlapping owner; reject stale
   guard/process state rather than overwrite it. Guard spawn, guard.assign,
   and touch_scratch_dir_usage failures with process/guard cleanup.

3. **Manual code could not be written in the UI** — generated Python was
   displayed read-only. A user wanting a single-line fix had to request an
   entire model regeneration, with another latency/cost cycle and unintended
   changes. Provide a Markdown-style **Edit Python / Write Python** editor,
   an acknowledged `setCodeSandboxCode` scene intent, optimistic compare
   against the original script, undo/redo, and `.py` export. The editor
   refuses writes during execution or approval and retains unsaved text
   after a rejected save. Clearing old output/analysis makes code-version
   provenance explicit. `Run saved script` bypasses *model regeneration*,
   **not** the existing mandatory human approval gate.

4. **Unbounded inputs and generated scripts** — requirements and generated
   Python could occupy arbitrary in-memory state, disk writes, snapshot
   payloads and approval dialogs. Explicit caps now apply at the domain,
   generation/repair, and execution edges. These quotas cannot replace
   the OS resource guard, but prevent accidental/hostile grossly
   oversized graph input.

5. **Raw subprocess encoding** — native locale-decoding can fail on mixed
   bytes from arbitrary scripts, terminating the output reader early. Use
   a deterministic UTF-8 text stream with replacement for bad bytes instead
   of failing with partial output or a lost reader.

## Acceptance criteria for this increment

- stdout with a single newline-free giant line never grows unbounded;
  exceeding the quota stops and reaps the subprocess with an explicit error
- finite output still returns exact text and preserves ordered per-chunk
  streaming; invalid UTF-8 is replaced, not swallowed
- one runner instance can never acquire two overlapping process guards;
  spawn/assignment failures clean up and allow a subsequent retry
- manual code edits compare against the currently saved source, are
  undoable, and fail during approval/execution; source edits invalidate
  output/analysis that belonged to the old code
- direct execution of a saved script still requires per-run human approval
- source-build opt-in, dependency validation, process-group/Job Object
  handling, cancellation and all existing tests remain intact
- Windows Python lint/mypy/pytest/coverage/perf/pip-audit and frontend
  lint/typecheck/vitest/build, Playwright, packaging and POSIX checks green

## Further high-impact phases (not included here)

**Strong execution containment.** Offer an *opt-in external isolated runner*
(container, VM, or platform-native sandbox) with a separately validated
permission and network model. Default venv mode remains visibly labelled
unisolated, rather than claiming stronger guarantees. Never silently grant
user folders/network access to AI-authored code.

**Reproducible dependencies.** Separate a user-approved, content-addressed
environment lock from free-form requirements, support freeze/export of
resolved versions+hashes, and verify the actual environment at reuse time.
Warn explicitly when reusing dependencies previously built from source.
Cache by interpreter version/platform plus lock, not requirements text
alone. Include native wheel provenance and vulnerability visibility.

**Execution workspace UX.** Dedicate a larger script editor and terminal,
show prior runs with code/manifest fingerprints, exit code and resource
stats, allow rerun of a pinned version and diff approval of AI repairs,
and offer one-click copy/export of reproducibility bundles.

**More robust process accounting.** Bounded log storage with an explicit
artifact export, backpressured WebSocket streaming, CPU and wall-clock
limits across the full generation/install/execute/repair pipeline, and
tests of worker cancellation while subprocesses spawn/exit concurrently.
Keep user secrets out of structured diagnostics.

**Measured quality improvements.** Offline workload tests for time-to-run,
dependency cache effectiveness, stream throughput and terminal UI latency;
report quantitative results only after recording a baseline.

All subsequent phases should use separate, non-overlapping PRs with full CI
and human review. No user code should ever execute merely because an AI
created or revised it.
