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
execution fails closed rather than falling back to the host. A new selection
applies only to a subsequent task, never to an already-running task.

## What always asks

Automatic shell commands in Autonomous are limited to literal `echo`,
`pwd`, `ls`, `cat`, `head`, `tail`, `wc`, `touch`, and `mkdir`
forms, with constrained flags and any file arguments resolved within the
bound workspace. Scripts, build tools, Git commands, interpreter invocations,
shell wrappers, ambiguous grammar and symlink escapes do **not** qualify.
A remembered shell approval cannot defeat these protections. Even when a
v2 exact command rule has been stored, scripts, Git, interpreters and other
commands outside the safe-shell grammar still require a fresh prompt.

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

Auto-edit and Autonomous file operations still pass scoped argument snapshots
to their handlers. File tools independently confine paths and reject VCS metadata.
For approval-pending `fs.write` and `fs.edit`, the request shown to the
user must be the request executed; mutated arguments cause denial.

## Exact shell-command rules and sessions

For an eligible `shell.exec` prompt, **Always allow** saves a v2 **exact
whole-command** rule, not a prefix. It binds the effective shell, execution
profile and hash of the resolved workspace root. In Isolated, the effective
shell is `/bin/sh`. Changed arguments, recomposed chains, workspace/profile
changes, symlink escapes and legacy prefix records cannot reuse approval.

The live sensitive-call gate is independent of the exact-rule matcher: a
saved rule can skip an approval only for a command that also passes today's
narrow safe-shell policy. Other commands can still prompt even if a rule
was saved earlier. Isolated Autonomous can skip safe `shell.session` starts
and read/list/stop operations, but **every stdin write** asks again.
Allowed exact commands can be removed in Agent settings. All grants are
cleared when a saved or imported graph is opened, including valid v2 rules.

## Isolated network and package installation

Isolated `shell.exec` is offline by default. Ask/Auto-edit support a
one-command network approval, a task-wide approval, or an explicit
**Allow network for every task** setting for the live graph. Autonomous
clears broad network consent and asks **once per network command**; neither
`shell.session` nor `python.exec` gets container networking. Web Research
uses separate task-scoped per-service and per-hostname approvals.

Autonomous may install compatible cached wheels with `python.install`
offline without prompting. If a wheel is missing, it asks for fresh consent
bound to the exact packages and interpreter **before** contacting PyPI.
Ask/Auto-edit prompt for every install, including cached installs.

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
