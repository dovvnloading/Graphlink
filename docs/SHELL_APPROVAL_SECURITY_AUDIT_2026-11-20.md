# Independent security audit: workspace-agent shell approvals and allow rules

**Date:** 2026-11-20  
**Repository:** \`dovvnloading/Graphlink\`  
**Baseline:** \`main\` at \`625709f03638be3866a268737970493c3568b1ae\`  
**Scope:** \`shell.exec\` approval and remembered allow decisions; \`shell.session\` consent boundaries; command parsing/disclosure for Bash, cmd and PowerShell; graph-file rule restoration; workspace/profile/shell binding; interaction with Isolated network consent.

This is a **source-level independent review**, not a live exploitation or
full operating-system sandbox assessment. Tests were added but were **not run
in this GitHub connector-only environment**. Runtime/CI status is unverified.

## Threat model

The user grants access to a directory and approves concrete shell actions.
The workspace agent controls commands and their arguments; workspace files,
imported graph/session data and model output are untrusted. Reusable consent
must never permit additional unreviewed shell code or operate on another
workspace, shell grammar or execution profile. A command outside the
remembered subset may execute only under fresh approval.

A Host shell starts with the workspace as its \`cwd\`, but \`cwd\` is **not**
a security boundary for arbitrary processes. The dedicated \`fs.*\` tools
use \`resolve_under_root\` with symlink-aware containment; Host \`shell.exec\`
does not and cannot safely emulate that via simple command-string parsing.
Isolated \`shell.exec\` uses a separately constrained container mount.

## Findings and fixes

| Severity | Finding at baseline | Fix |
| --- | --- | --- |
| **High** | Remembered prefix rules used \`segment.startswith(prefix)\` plus an argument boundary. An approved \`ls\` could silently authorize \`ls /etc\` or \`ls ../private\`; an approved \`git status\` could authorize a different status invocation with new flags or paths. A general executable prefix could authorize unreviewed effects. | Replaced prefix matching with exact per-segment grants. Restrict grants to a small fixed-shape, plainly read-only command set. Arbitrary paths, extra flags, scripts, wrappers and interpreter execution require fresh consent. |
| **High** | Rules stored only \`effective-shell<TAB>prefix\`, omitting the workspace root and execution profile. Consent granted in scratch could automatically apply after rebinding to a trusted user directory, and a rule could cross a Host/Isolated profile boundary if the shell label matched. | Bind live grants to resolved root, profile and actual interpreter. An Isolated \`shell.exec\` runs \`/bin/sh\` in the container even if the Host shell setting is different. Missing/unresolvable roots cannot create or match grants. |
| **High** | The graph/session loader accepted persisted shell rules as authority for silent execution. A graph file can be imported or hand-edited, so even a well-formed saved rule is not proof the local user consented. | Never serialize live grants as consent and ignore all \`shell_allow_rules\` data on load, including legacy prefixes and forged new-format grants. This intentionally changes "Always allow" to **live-session only**. |
| **Medium** | The legacy cmd/POSIX splitter was a separator regex with no quote/escape grammar. For example \`echo "a;b" & rm -rf out\` was displayed as artificial fragments \`echo "a\` and \`b"\`, so the approval disclosure was not faithful to the actual shell syntax. Script and wrapper first words also fell outside destructive-command detection. | Treat quoting, escaping, expansion, comments and other ambiguous cmd/POSIX forms as **unsplittable and dangerous by default**. Disclose the entire original command under fresh approval rather than assert a misleading parse. Classify additional wrappers/dispatchers and directory-changing builtins as dangerous. |
| **Medium** | Segment-level approval of a chain could be reused across different, unseen arguments because every segment merely matched a stored prefix. The splitter alone was not a complete security parser. | Require every segment in a chain to independently match the exact scoped rule. Reject any chain containing unsupported punctuation, arbitrary path operands, or dynamic shell syntax from saved consent; the entire invocation then prompts. |
| **Documentation** | UI and docs described persistent "allowed command prefixes" and implied every shell command prompts, neither of which accurately described the repaired contract. | Updated settings and approval copy; documented runtime-only exact grants, profile/shell/root binding and the Host filesystem limitation. |

### Concrete negative examples

With the old matching behavior, a rule for \`ls\` could match \`ls /etc\`
or \`ls ..\`; \`git status\` could match a modified \`git status --...\`
invocation. A chain consisting of previously granted prefixes could also
pick up unreviewed arguments. After this change, none of these variants
reuse the original grant. \`cd .. && git status\`, \`git -C ../other status\`,
\`python -c ...\`, shell substitutions, escaped metacharacters and quoted
chains are ineligible for remembered approval even when they could be
approved once. These are **authorization/disclosure proof cases based on
code inspection**, not claims of observed exploit execution.

### Existing boundaries checked

- The registry's fresh-consent predicate prevents tool-wide session grants
  or a cached fingerprint from authorizing arbitrary \`shell.exec\`.
  A rule lookup now also needs a live bound root and matching context.
- Shell \`start\` and \`write\` still require fresh consent; interactive stdin
  cannot be authorized by a saved shell command rule.
- Isolated shell network consent is distinct from command consent:
  requesting \`network: true\` does not acquire it solely from a command
  grant. A separate explicit per-task/node network authorization is needed
  before the container runs with bridge networking.
- Shell approval panels disclose all segments when safely parseable, or
  the full original text with an ambiguity warning. Approval tokens guard
  the pending shell interaction; stale/cancelled decisions cannot create
  reusable consent.
- The trusted-workspace gate validates explicit folder selection before a
  run; standalone \`fs.*\` tools check real-path containment. This does not
  make arbitrary Host subprocesses a sandbox.

## Regression tests included

- \`backend/tests/test_harness_h6.py\`: exact matching, per-segment chains,
  rejected extra flags/paths/aliases/inline interpreters, roots, effective
  shell and execution profile, and the registry's actual prompt behavior.
- \`backend/tests/test_harness.py\`: graph serialization emits no reusable
  consent and edited/imported legacy or forged grants are dropped.
- \`backend/tests/test_shell_policy_powershell.py\`: legacy Bash/cmd quote
  splitting fails closed instead of fabricating statements; existing
  PowerShell quote/nesting/alias cases remain exercised.
- \`web_ui/src/app/canvas/HarnessNodeView.test.tsx\`: rule-list labeling
  reflects exact session-scoped permissions.

Suggested focused commands for maintainers:

\`\`\`bash
python -m pytest -q backend/tests/test_harness_h6.py backend/tests/test_harness.py backend/tests/test_shell_policy_powershell.py
python -m ruff check backend/harness/shell_policy.py backend/harness/loop.py backend/tools.py backend/session_load.py backend/session_save.py backend/tests/test_harness_h6.py
cd web_ui && npm run test -- src/app/canvas/HarnessNodeView.test.tsx
\`\`\`

**Verification status:** Not executed locally because this review used the
GitHub repository API without a runnable checkout. Passing CI, a real
approval-flow UI run, and Windows PowerShell/cmd and container integration
checks remain necessary. The request explicitly directs that CI **not be
awaited after the PR is opened**.

## Residual risks, behavioral changes and release posture

1. **Host shell commands are not workspace-confined.** Once separately
   approved, arbitrary Host shell code can traverse \`..\`, follow links,
   access absolute paths, or invoke external tools. The patch closes
   unattended reauthorization, not the Host execution capability itself.
   Choose Isolated when filesystem confinement is required.
2. **Approved executable behavior may change.** Even exact command text
   can execute different external binaries or repository-configured helpers
   after executable/configuration changes (e.g. a Git fsmonitor hook).
   This is not a formal read-only attestation or a trust boundary over all
   subprocess behavior. Further hardening could restrict remembered grants
   to shell builtins or execute them through an OS-enforced read-only sandbox.
3. **Graph-file compatibility deliberately changes:** formerly durable
   allow-prefix rules are discarded on save/load. The user must explicitly
   approve commands again after a reload; the UI describes this tradeoff.
4. **Isolated container boundaries have separate assumptions:** container
   runtime/image trust, platform-specific bind mounts, daemon privileges,
   path escape through the engine and approved networking are not proven
   by this review. Shell/session profile selection and network grants
   are outside the scope of the exact-match rule policy.

**Recommendation:** Security-sensitive change; require maintainer review
and passing relevant tests and platform validation before release.
