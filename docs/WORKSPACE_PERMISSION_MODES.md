# Workspace agent permission modes

The workspace Agent card exposes a **Permission mode** choice in **Agent settings**.
The mode is a preference for the *next task*, not a change to the running task.
The backend snapshots the effective mode and execution profile when it claims
a task. The card shows the most recent task's mode, and the workspace's
append-only `transcript.jsonl` records a `task` audit line with the run ID,
effective mode and execution profile before that task's user message.

| Mode | Where available | Approval behavior |
| --- | --- | --- |
| **Ask** (default) | Host and Isolated | Existing per-tool prompts and explicit saved grants |
| **Auto-edit** | Host and Isolated | Workspace-confined `fs.write` and `fs.edit` can run without prompting |
| **Autonomous** | **Isolated only** | Includes Auto-edit, plus vetted workspace shell commands and Python source, cached offline Python package installs, and harmless shell-session management |

Autonomous cannot be selected under **Host**. The backend rejects that setting
even if the UI is bypassed, and an invalid or legacy saved profile/mode
combination safely falls back to Ask. Switching from Isolated to Host resets
an Autonomous preference to Ask. A task started in a stricter mode retains
that mode even if the user selects a looser preference while it runs.

## Approvals that never disappear

The permission mode does **not** change the agent's tool scopes or the
workspace root, and it does not grant network access. These operations
continue to require explicit permission:

- A file write/edit targeting a path outside the bound workspace (including
  paths escaping through symlinks). The underlying file tool still enforces
  confinement and may reject the operation after the approval question.
- Dangerous, ambiguous or destructively mutating shell commands, including
  command substitution, unreviewable syntax, `rm -rf`, and `git push`.
  Outside-workspace paths and commands that may access the network also
  trigger a fresh prompt, even if an earlier task saved a shell-prefix rule.
- Python source that may access a network, use an outside-workspace path,
  run an external command, dynamically evaluate code, or cannot be
  confidently screened. The scanner is deliberately conservative: complex
  Python scripts may still prompt in Autonomous.
- Every requested isolated shell network connection in Autonomous. The
  existing broad network setting is cleared on entering Autonomous; its
  prompt permits only a single request, never the whole task. Web Research
  preserves its per-domain task approval and redirect consent.
- On `python.install`, a cached-wheel **offline** install can run without a
  prompt. When a wheel is missing, the installer **stops before contacting
  PyPI** and asks for fresh approval tied to the exact interpreter and
  package list. Only after approval may the downloader access PyPI.

The same isolation container, resource limits, process guards, workspace
binding and per-tool cancellation rules remain in place. Auto-approval is a
narrow exemption in the central ToolRegistry *after* the scope gate, not a
new scope grant. Untrusted tool output and workspace contents cannot change
the effective mode of an in-progress task.

## Migration and compatibility

Previously saved Agent nodes default to **Ask**. New state fields:
`harness_permission_mode` is the durable preference and
`harness_task_permission_mode` is the recorded effective mode of the last
task. Transcript `task` rows are audit-only and ignored by model-history
loading; older transcripts load unchanged. Existing shell allow rules and
per-tool consent continue to operate under Ask/Auto-edit, subject to the
new sensitive-path/command checks.
