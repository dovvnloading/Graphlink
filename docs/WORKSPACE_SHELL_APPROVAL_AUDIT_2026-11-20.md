# Independent audit: workspace shell approvals and remembered rules

**Date:** 2026-11-20  
**Area:** workspace agent shell.exec approval gating, shell.session disclosure, node-persisted allow rules, session reload, and Host/Isolated shell selection.  
**Method:** inspected the request/decision flow from backend/harness/loop.py through backend/tools.py, backend/harness/shell_policy.py, shell launchers, session load/save, and node settings. Added adversarial regression tests; no exploit commands were executed against a user machine.

## Findings and corrective actions

| ID | Severity | Finding before this change | Mitigation in this change |
| --- | --- | --- | --- |
| SA-01 | High | A persisted shell+text *prefix* could authorize new argument tails. For example, an approval for a harmless Git invocation could silently expand to invocation flags that change the path it reads or operates in. | Replaced prefix matching with an exact whole-command match. Changing even one argument requires a new approval. |
| SA-02 | High | Each segment of a newly composed chain was independently matched against previously saved prefixes, so individual approvals could be recombined into a different pipeline/chain without a fresh decision. | The entire command string, including separators, is now one rule. Segments are screened separately for dangerous syntax, but different chains are never assembled from prior approvals. |
| SA-03 | High | Saved rules were scoped to the effective shell only. Changing the node's bound workspace (scratch vs. trusted directory) or its execution profile could carry old silent approvals into a different filesystem or host/container trust context. | Versioned v2 rules bind the effective interpreter, Host/Isolated profile, and a hash of the resolved workspace root. A rebind or profile change cannot reuse a previous rule. Isolated shell.exec is classified as its actual /bin/sh, not the Windows host's PowerShell preference. |
| SA-04 | High | Persistent approval checks did not reject absolute/parent paths or symlinks that leave the bound workspace. Existing prefixes could also accept path-related flags not reviewed in the original approval. | Saved-rule eligibility now rejects traversal, absolute/drive/URI paths, opaque option forms, script wrappers, redirects, substitutions and other ambiguous syntax. Literal path/option values are resolved under the workspace root at each reuse; a symlink redirected outside invalidates the skip. |
| SA-05 | Medium | POSIX/cmd disclosure split naively on separators even inside quoted strings. cmd also needs different treatment of apostrophes (which are not quotes). Ambiguous syntax could be misrepresented as independently reviewed statements. | Introduced quote-aware POSIX/cmd segmentation; escape, expansion, grouping, comments and unmatched quotes are treated as unsplittable and dangerous-by-default. cmd apostrophes are not treated as quote delimiters. PowerShell keeps its conservative parser. |
| SA-06 | High | Session reload accepted legacy shell+prefix rules with no workspace/profile binding. | #723 made legacy records inactive and restricted rule shape to v2. The later #725 security audit now clears **all** active rules (including valid v2) whenever a saved/imported Agent graph opens. |

## Approval boundary and compatibility

- The exact-rule matcher validates workspace/profile/shell-bound commands. Since #725, the live sensitive-call gate independently allows only a narrow, literal safe-shell grammar; `shell.session` starts can be exempt in Isolated Autonomous only when safe, while stdin writes always require approval. Network consent is separately gated.
- The **Always allow** UI now describes exact, workspace- and profile-bound commands. The settings view exposes the shell and profile while allowing rule revocation.
- This is a deliberately stricter compatibility change: previously saved prefix rules will no longer bypass prompts. Users must explicitly approve a command again to create a v2 rule. A separately approved new argument or a newly composed chain also prompts.
- Rules are a consent convenience, **not** a claim that the command cannot change files. Even an exact command can have different effects if scripts, project configuration, executables or environment state change.

## Superseded integration observation and the #725 boundary

At the #723 baseline, the sensitive-call helper looked for exact-rule candidates without passing the required `workspace_root`, causing an empty candidate list and extra prompts even for safe commands. **This observation is historical, not a current-main limitation.** PR #725 replaced that candidate-based shortcut with an independent, narrowly parsed shell allowlist and made the sensitive gate require a fresh prompt for everything else. In Isolated Autonomous, harmless literal shell calls and safe session starts can now bypass per-call approval; `python.exec`, script/interpreter launches, Git commands and shell stdin cannot.

An eligible v2 rule may still be saved from an `shell.exec` approval, but it can suppress a later prompt only if that command also passes the current sensitive-call checks. Rules do not survive reopening a saved/imported graph. The matcher tests from this audit establish exact rule matching, not blanket exemption from the newer sensitive-call policy. See [permission modes](WORKSPACE_PERMISSION_MODES.md) and [the #725 audit](WORKSPACE_PERMISSION_MODES_AUDIT.md).

## Regression coverage added or updated

- Exact matching versus argument suffixes, chain composition, reordered operators and dangerous chained tails.
- Workspace rebinds, Host/Isolated profile transitions, unknown-shell rejection and actual container /bin/sh grammar.
- Relative parent traversal, absolute Unix/Windows paths, option-embedded paths, symlink-to-outside and symlink retargeting after saving.
- Bash/cmd quoting, cmd apostrophes, substitutions, escaping, interpreter wrappers, redirect syntax, PowerShell nested commands and destructive cmdlets.
- Live ToolRegistry approval/denial, legacy session rule rejection and node settings rendering/removal.

Relevant tests: backend/tests/test_shell_allow_security_audit.py, backend/tests/test_harness_h6.py, backend/tests/test_shell_policy_powershell.py, backend/tests/test_harness.py, web_ui/src/app/canvas/HarnessNodeView.test.tsx.

**Execution evidence:** Tests were added and statically reviewed, but were **not executed locally**: this audit environment had GitHub connector access without a runnable repository checkout. Per request, the PR is opened without waiting for CI. No passing-test or CI claim is made here.

## Remaining limits / out of scope

**Host execution is not a filesystem sandbox.** shell.exec runs a user-approved program with workspace cwd and a scrubbed environment; the host OS does not restrict that process to the workspace. A command explicitly approved *once*, a tool that interprets its own configuration, an executable modified after approval, or an access raced between path checking and spawning may still touch outside files. Path validation here protects the **remembered approval shortcut**, not arbitrary shell execution. Use the **Isolated** profile (with its bounded container mounts) where OS-enforced separation is required; review any additional mounts and network grants separately. Local session files are not cryptographically authenticated against deliberate modification by a person with access to the graph file.

No CI status was consulted or awaited as part of this audit.
