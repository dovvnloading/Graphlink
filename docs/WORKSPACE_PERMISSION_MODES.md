# Workspace agent permission modes

The workspace Agent card offers **Ask**, **Auto-edit** and **Autonomous** in
Agent settings. The backend snapshots the effective mode and execution
profile at task claim, so changing a preference mid-task never loosens that
task. The card shows the previous task's mode; an append-only
`transcript.jsonl` task row records each run's mode and profile.

| Mode | Profile | Operations that can skip approval |
| --- | --- | --- |
| **Ask** (default) | Host or Isolated | Ordinary tool policies and grants explicitly made during the current session |
| **Auto-edit** | Host or Isolated | Workspace-confined `fs.write` and `fs.edit` |
| **Autonomous** | **Isolated only** | Auto-edit plus a narrow list of offline shell commands, harmless shell-session management and offline cached-wheel Python installs |

Autonomous cannot be selected for Host. If a container engine is missing,
execution fails closed rather than falling back to the host.

## What always asks

Automatic shell commands in Autonomous are limited to literal `echo`,
`pwd`, `ls`, `cat`, `head`, `tail`, `wc`, `touch`, and `mkdir`
forms, with constrained flags and any file arguments resolved within the
bound workspace. Scripts, build tools, Git commands, interpreter invocations,
shell wrappers, ambiguous grammar and symlink escapes do **not** qualify.
A remembered shell approval cannot defeat these protections.

**Every `python.exec` invocation asks for approval.** Python runs in a
persistent REPL; source scanning cannot prove a prior alias, imported
callable or dynamically generated pathname is safe, even when the visible
source looks harmless. Re-enabling automatic Python would require
enforceable runtime constraints rather than just an AST allowlist.

Network requests from Isolated Autonomous shell calls require approval for
the **specific command**, not a task-wide or saved grant. Web Research
keeps its per-domain approvals and redirect consent. For `python.install`,
a cached-wheel install can happen entirely offline without prompting.
If the cache is insufficient, it stops before a PyPI connection and
requests fresh approval for the exact packages and interpreter.

File tools still independently confine paths and reject VCS metadata.
For approval-pending `fs.write` and `fs.edit`, the request shown to the
user must be the request executed; mutated arguments cause denial.

## Saved graphs and archives

**A saved or imported graph is data, not an authorization token.** The
Agent's live permission preference resets to **Ask** when a graph opens;
serialized shell rules and broad network preferences are cleared. Users
must select a new mode or approve new grants during that open session.
The serialized previous task mode survives solely for the display and
audit record. Workspace archive import also scrubs these grant-bearing
fields from stored graph rows before they are opened. Older transcripts
remain readable and task-audit rows do not become model conversation turns.

See [the independent PR 721 audit](WORKSPACE_PERMISSION_MODES_AUDIT.md)
for findings, tests and remaining limitations.
