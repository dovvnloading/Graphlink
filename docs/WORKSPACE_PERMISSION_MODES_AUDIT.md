# Independent audit of PR 721 workspace permission modes

**Repository:** `dovvnloading/Graphlink`
**Baseline:** merged PR #721 (`917ac8136d675778838f898a1995606349dbc31b`).
**Integration:** main also contains PR #723's exact, root/profile-bound
saved shell rules and improved shell disclosure. This audit preserves that
hardening rather than restoring old prefix semantics.

## Findings and corrections

| Severity | Confirmed risk | Remediation in this PR |
| --- | --- | --- |
| High | Autonomous shell policy could treat interpreter script launchers (`bash ./run.sh`, `python ./build.py`), wrappers and Git helpers as harmless. Looking for network keywords or path-like tokens cannot prove a general-purpose executable is safe. Bare symlinked file arguments were also missed. | Only literal, conservatively parsed shell builtins/file operations can skip approval. Check *all* file operands against the real bound workspace, including bare names and symlinks. Analyze Isolated commands as the actual `/bin/sh` container shell. |
| High | Autonomous Python AST screening was bypassable with aliased `io.open` and an assembled outside-workspace path. A persistent REPL can also shadow a trusted-looking name in an earlier turn, invalidating source-only checks. | Require a fresh human approval for **every** `python.exec`. This is intentionally stricter than PR #721: a persistent Python interpreter cannot be confined by inspecting each submitted source string alone. |
| High | Serialized `permission_mode=autonomous` under the Isolated profile automatically reactivated on load, together with valid saved shell rules and a broad network preference. An imported archive could carry those values. | Loaded Agent nodes start in Ask with empty shell approval grants and no broad network grant. Preserve the previous task's mode only as audit metadata. Archive import additionally scrubs grant-bearing fields before database insertion. |
| Medium | `fs.write`/`fs.edit` approval previews used a mutable argument dictionary. It could change while approval was pending; unprompted Auto-edit also passed a mutable caller-owned dictionary to the handler. | Snapshot file arguments before policy and preview. Refuse changed pending approval requests, and use the immutable execution snapshot for automatically approved operations. |

## Boundaries checked and retained

- The dispatcher freezes effective mode and profile at task claim; modifying
  the preference mid-run cannot loosen that already-running task.
- `effective_permission_mode` refuses Autonomous unless the profile is
  Isolated, and container execution never silently falls back to Host.
- The scope gate still runs before mode exemptions; any sensitive or
  ambiguous command forces a fresh prompt over remembered rules.
- `shell.exec` with `network=true` in Autonomous requests token-bound
  consent **once per command**, even if the node previously had a broad
  network preference. By default isolated shell/Python execution is offline.
- `python.install` attempts an offline cached-wheel install first; on a
  cache miss the user must approve a package/interpreter-bound PyPI download.
- File tool handlers retain their existing root confinement and VCS guards.

## Regression tests and validation

Added `backend/tests/test_harness_permission_audit.py` for interpreter
launchers and wrappers, literal safe shell calls, bare-argument symlink
escapes, the Python alias bypass, loaded/imported grants, pending approval
argument mutation and Auto-edit snapshots. Updated the original permission
tests and save/restore expectations to require fresh approval on reopening.

**Execution status:** Tests were not run locally: the repository is accessed
through the connected GitHub integration and no executable checkout was
available. Suggested focused command:

`pytest -q backend/tests/test_harness_permission_modes.py backend/tests/test_harness_permission_audit.py backend/tests/test_harness_permission_install.py backend/tests/test_harness.py`

CI is not awaited, as requested.

## Remaining limitations

- Autonomous no longer runs arbitrary Python or shell scripts without human
  approval. A fuller Autonomous implementation requires a truly enforced
  execution boundary, not a growing denylist of syntax or module names.
- Cached wheels remain eligible for unprompted *offline* installation under
  the PR #721 contract; a cached package is not inherently trusted code.
- A concurrent hostile local process might swap a symlink between path
  validation and a filesystem write. Descriptor-relative no-follow I/O would
  further harden this existing race.
- A human may explicitly approve a dangerous or outside-scope command where
  the underlying tool permits it. This patch prevents silent consent reuse;
  it is not a host OS sandbox for explicitly approved Host commands.
