# Workspace agent permission modes

Open **Agent settings → Permission mode** on the workspace Agent card. The preference is for the *next* task, not the running task: the backend snapshots the effective mode and execution profile when claiming a task. The card shows the latest task's mode, and the append-only workspace `transcript.jsonl` records a `task` audit row with run ID, mode and profile before that task's user message.

| Mode | Where available | Current behavior |
| --- | --- | --- |
| **Ask** (default) | Host and Isolated | Ordinary per-tool prompts and eligible remembered grants. Workspace writes and Python execution prompt. |
| **Auto-edit** | Host and Isolated | Confined `fs.write` / `fs.edit` skip approval; other tools retain Ask behavior. |
| **Autonomous** | **Isolated only** | Includes Auto-edit, screened `python.exec`, offline cached-wheel `python.install`, and `shell.session` read/list/stop. On current `main`, shell commands and session starts still prompt despite the intended screened exemption (below). |

Autonomous is rejected under Host, even if the UI is bypassed. Unknown or invalid saved values fall back to Ask; switching Isolated to Host resets an Autonomous preference to Ask. A running task never acquires a looser setting selected during that task.

## Approvals and boundaries that remain

A mode never changes the granted workspace or tool scopes. ToolRegistry checks scopes before exemptions, while handlers retain path confinement, execution guards and cancellation.

- File writes/edits with outside-root paths (including symlink escapes) cannot skip approval and may be blocked by the file handler even after a prompt.
- Dangerous or ambiguous shell commands—e.g. `rm -rf`, `git push`, redirections, shell substitutions and unreviewable wrappers—require a fresh decision. Commands that may access the network or paths outside the workspace also prompt. Shell-session stdin **always** needs fresh approval.
- Isolated Autonomous `python.exec` skips a prompt only if its source passes conservative AST, path, network and subprocess screening. Code involving uncertain operations, network, dynamic execution or outside paths prompts. Ask/Auto-edit ask for every execution.
- In Isolated Autonomous, `shell.exec` network consent is **per command only**: the panel offers **Allow network once**, not a task-wide choice. Enabling Autonomous clears the broad per-node network grant. Isolated Ask/Auto-edit also support one-command or task-wide consent and a separate **Allow network for every task** opt-in. `shell.session` and `python.exec` remain offline in all Isolated modes.
- Autonomous `python.install` first attempts a network-disabled install from already cached, compatible binary wheels without prompting. If the cache is insufficient, it stops before PyPI and asks for fresh interpreter- and package-bound approval for the binary-wheel download. Ask/Auto-edit require approval even for cached installs.
- Graph mutations, scientific runs and Web Research keep their ordinary approvals, including per-service/per-hostname Web Research task consent and redirect checks.

## Remembered exact shell commands: current-main caveat

For eligible `shell.exec` prompts, **Always allow** saves a versioned **v2 exact whole-command** rule—not a command prefix—bound to the effective shell, execution profile and a hash of the resolved workspace root. In Isolated the effective shell is `/bin/sh` even on Windows. Changing arguments, composing separately approved commands into a chain, switching workspace/profile or retargeting symlinks cannot reuse a rule. Unsafe syntax, absolute/parent paths, ambiguous options, inline interpreters, environment wrappers and PowerShell comments are ineligible. Legacy prefix records are inactive.

**Post-#723 integration limitation on `main` (`c2551273`):** `backend/harness/permission_modes.py::_safe_shell()` calls `shell_allow_rule_candidates()` without the required `workspace_root`. It consequently receives no candidates. Its sensitive-call check makes **all** workspace `shell.exec` and `shell.session` starts request fresh approval, even if the exact matcher would otherwise accept a saved rule. The intended Autonomous safe-shell/session-start exemptions do not currently apply. A rule can be saved, but **does not currently suppress prompts**. This is fail-closed extra prompting, not a wider grant. See [shell audit](WORKSPACE_SHELL_APPROVAL_AUDIT_2026-11-20.md).

## Migration and compatibility

Existing nodes without mode fields default to Ask. `harness_permission_mode` stores the durable preference; `harness_task_permission_mode` records the effective mode of the most recent task. Transcript `task` records are audit-only and do not enter model history. Old transcripts still load. Only valid v2 shell rules are active storage entries, and they can be revoked under **Agent settings → Allowed exact commands** (subject to the integration caveat above). Other eligible per-tool consent remains gated by sensitive-call checks.
