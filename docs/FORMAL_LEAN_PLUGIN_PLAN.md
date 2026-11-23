# Graphlink — Formal Mathematics with Lean 4

**Engineering design, security model, delivery roadmap, and acceptance specification**  
**Status:** Proposed; no implementation or runtime verification is claimed  
**Prepared:** 2026-11-23  
**Target repository:** [dovvnloading/Graphlink](https://github.com/dovvnloading/Graphlink)  
**Repository baseline reviewed:** [`main` at `9cc59d88583da59bef3f2cf00b33389ec8f5daaf`](https://github.com/dovvnloading/Graphlink/commit/9cc59d88583da59bef3f2cf00b33389ec8f5daaf)  
**Proposed feature ID:** `formal_lean`  
**Proposed node kind:** `formal_lean.proof`  
**Primary principle:** No proof is reported as verified unless the exact formal proposition is checked under a reproducible, explicitly bounded trust policy.

> **Accuracy statement.** This is a detailed implementation plan based on inspection of the cited repository revision and upstream Lean documentation. It is not a completed plugin, security certification, tested Lean command transcript, or guarantee of mathematical soundness. Every unresolved technical choice is marked as a gate or a proposal. Before implementation, confirm APIs, platform support, and reproducer behavior against the chosen *pinned* Lean release. Never substitute an untested example command for passing integration evidence.

---

## 0. Executive decision record

**Decision D-01 — Build a new plugin, not a replacement for Mathematics.** Graphlink already ships `science_mathematics`, which numerically samples polynomials, integrates paired observations, and solves small dense systems. Formal verification is a distinct task. Keep existing operation IDs and results unchanged; add a separate opt-in **Formal Mathematics (Lean 4)** entry.

**Decision D-02 — Lean 4 is the proof checker; Graphlink is the orchestrator and visual workspace.** Graphlink may edit text, propose proofs, create graph nodes, display diagnostics, and store provenance. Only Lean's checker may establish that a formal proof term inhabits a specified proposition. An LLM, a numeric result, a proof-shaped explanation, or a tool process exit code alone cannot establish a verified theorem.

**Decision D-03 — Exact propositions take precedence over natural-language summaries.** Store/show the actual Lean statement, its elaborated type or canonical representation, named dependencies, and the precise environment in which it was checked. If the user asks to prove a natural-language claim, Graphlink must explicitly separate the human-language interpretation from the formal theorem actually verified.

**Decision D-04 — User Lean code is untrusted executable code.** Even if it is “only a proof,” Lean tactics, macros, elaborators, and imports may execute computations or access I/O. Execute all user-controlled Lean material only in a tested container or equivalent strong isolation; the existing Graphlink plugin worker by itself is **not** such a sandbox. Fail closed if isolation is unavailable.

**Decision D-05 — Proof checking and axiom-policy compliance are separate.** A kernel-accepted theorem may depend on accepted axioms, `sorryAx`, new axioms, or trust-extending facilities. Strict verification requires a reviewed dependency/axiom policy as a second gate, with an independent regression specifically covering Lean upstream [issue #15226](https://github.com/leanprover/lean4/issues/15226), which documents under-reporting in `Lean.collectAxioms` / `#print axioms` for certain imported declarations.

**Decision D-06 — Save an immutable verification receipt, never a timeless green badge.** The receipt binds source, formal statement, imported dependencies, Lean binary, mathlib revision, execution policy, and checker outcome. Any change makes the previous result **stale**, not verified for the new inputs.

**Decision D-07 — Keep v1 independent of model providers.** Users can type and verify Lean proofs offline. AI-assisted suggestions, mathlib search, and automatic proof attempts are optional later phases; a model's output always returns to the same verification gate.

**Go/no-go recommendation:** Start with a gate-driven MVP: proof node + editor + sealed Lean environment + verified receipts + negative security tests. Do not market this as “formal mathematics verification” until all release gates in §18 pass.

## 1. Product definition and non-goals

### 1.1 User problems

1. A mathematician wants to express a theorem in Lean, develop its proof, and see authentic compiler/kernel diagnostics.
2. A learner wants to explore goals and hypotheses while being told accurately whether a proof is complete.
3. A researcher wants a versioned, reproducible record of which exact formal claims have been checked.
4. An AI-assisted workflow needs a hard boundary between speculative proof suggestions and accepted machine-checked proofs.
5. A Graphlink user wants to branch a theorem into alternatives and compare attempts without losing provenance when an upstream node changes.

### 1.2 MVP scope

- Optional **Formal Mathematics (Lean 4)** plugin and canvas proof node.
- User-editable *formal statement* and *proof candidate*, with the formal statement prominently displayed.
- Pinned, offline Lean + compatible mathlib environment selected and provisioned outside routine execution.
- Explicit **Check proof** and **Cancel** actions; structured diagnostics and source location support.
- Rigorous proof status states and preserved immutable receipts.
- Configured policy for permitted axioms/declarations; automatic fail-closed behavior on audit ambiguity.
- Save/reload, undo/redo, grant revocation, stale-graph, and reproducibility behavior.
- JSON/text receipt export; Lean source export only after sanitization and scope review.
- Accessibility, Windows/Ubuntu validation, and packaged-wheel tests.

### 1.3 Deliberate non-goals for v1

- No claim that informal LaTeX or English has been proved merely because an associated Lean file compiles.
- No automatic trust in LLM proof generation, `omega`, `simp`, or other tactics independently of final kernel checking.
- No arbitrary host filesystem imports, arbitrary network dependencies, `lake update`, or package downloads during proof checking.
- No unconstrained access to local Lean executables or host installations.
- No unreviewed native code/FFI, arbitrary custom `lakefile`, external mathlib forks, or custom global axioms.
- No theorem dependency graph rewrite of Graphlink's core graph schema in v1.
- No attempt to prove floating-point numerical workbench outputs equal exact mathematical real-number theorems automatically.
- No Lean LSP multi-file project editing or collaborative package-management interface in v1; these are expansion candidates.

### 1.4 Key success measure

For every **Verified (policy-compliant)** result, an independent verifier supplied only the stored receipt, exact source, and sealed pinned environment must be able to reproduce the same formal theorem and checker verdict. A missing component, differing hash, rejected axiom, or invalid environment prevents that status.

## 2. Repository findings: confirmed baseline versus proposed addition

The following are code-reviewed observations at the baseline SHA, **not** assumptions about future branches.

| Existing component | Observed contract | Consequence for Lean |
|---|---|---|
| [`plugins/science_mathematics/plugin.toml`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/plugins/science_mathematics/plugin.toml) | Built-in, grant-gated Mathematics pack, SDK v1; `science_mathematics.workbench`. | Do not overwrite the existing pack or reuse its operation IDs. |
| [`graphlink_plugins/scientific/packs/mathematics.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/graphlink_plugins/scientific/packs/mathematics.py) | Numeric polynomial sampling, sampled integration, dense system solver. | Formal theorem proving must remain semantically distinct from numerical computation. |
| [`backend/plugin_sdk.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/backend/plugin_sdk.py) | `HostContext.register_node_kind`, `register_picker_entry`, `register_intent`; namespaced kinds; SDK API version 1; optional serializers. | Implement via public SDK, not bespoke global intent names or new core kinds. |
| [`backend/plugins.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/backend/plugins.py) | Grant checks, static `app-plugins/invokePluginIntent` dispatch, optional async handlers; `PluginSceneUpdate` publishes changes. | Lean-specific operations use registered plugin intents and existing grants. |
| [`backend/plugin_worker.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/backend/plugin_worker.py) | Separate Python worker can hide host process secrets, but does not enforce filesystem/network confinement. | Never run user Lean code in an unsandboxed plugin worker alone. |
| [`backend/domain/graph.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/backend/domain/graph.py) | Plugin live wire is `dict[str,str]`; generic node serialization hook is external to domain. | Expose versioned Lean UI state as a JSON **string** under a key such as `pluginState.formalLean`. |
| [`backend/session_save.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/backend/session_save.py), [`backend/session_load.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/backend/session_load.py) | Plugin state has generic save/load support; large state can be externalized; disabled/missing plugins have passive-state behavior. | Preserve proof text and evidence even when disabled; fail safely on missing assets. |
| [`backend/asset_store.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/backend/asset_store.py) | SHA-256-addressed, verified, atomic asset storage. | Reuse for bounded detailed logs/artifacts; do not invent a parallel binary store. |
| [`web_ui/src/app/canvas/SceneCanvas.tsx`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/web_ui/src/app/canvas/SceneCanvas.tsx) | Explicit node-view mapping; science workbench has special renderer; unknown kinds fall back to title-only placeholder. | Add a deliberate `isFormalLeanKind` renderer; a generic new node alone has insufficient UI. |
| [`web_ui/src/app/canvas/sceneStore.ts`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/web_ui/src/app/canvas/sceneStore.ts) | Client dispatches `app-plugins/invokePluginIntent` and tracks scene snapshots. | Add typed client wrapper and no unclassified new bus topic unless absolutely necessary. |
| [`web_ui/src/app/chrome/PluginPicker.tsx`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/web_ui/src/app/chrome/PluginPicker.tsx) | Dynamic backend-driven picker, grants, optional parentless spawn. | Discover Lean from its manifest and give it an appropriate category. |
| [`graphlink_execution_guard.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/graphlink_execution_guard.py), [`graphlink_process_env.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/graphlink_process_env.py), [`docs/WORKSPACE_ISOLATION.md`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/docs/WORKSPACE_ISOLATION.md) | Host resource guards and a separately supported isolated container mode for workspace commands. | Reuse hardened container orchestration ideas, but provision a **Lean-specific** environment and fail closed. |
| [`AGENTS.md`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/AGENTS.md) | Strict CI, UI, packaging, domain purity, undo classification, and change-discipline gates. | All new code must pass existing gates without relaxing budgets or tests. |

**Absence finding (bounded):** The audited tree/search did not reveal an existing Lean 4 verification implementation; “lean” in Graphlink text often means “small/lean architecture,” not the theorem prover. This is a proposed new subsystem, not a refactor of a hidden existing Lean engine.

### 2.1 Integration seam to preserve

A Graphlink plugin is declared in a `plugin.toml`, registers node kinds/picker entries/intent handlers through `HostContext`, and is activated subject to grants. The plugin's node `kind` becomes `<plugin_id>.<local_kind>`; serialization is supported through public hooks; mutable graph changes must use `SceneDocument.record_command`; the frontend reads validated scene payloads. Therefore:

- `formal_lean.proof` is a **plugin kind**, not a new core `SceneNode.kind` field family.
- Register dynamic intents such as `check`, `cancel`, and `export` inside the plugin. Reuse existing static `invokePluginIntent` bus route and its undo-classification boundary.
- The host owns source snapshots, permissions, stale-source checks, and undo; the container receives **only frozen proof text and public pinned toolchain assets**.
- The Lean result is committed atomically only if the node, revision, workspace, edges, and grant are still current.

## 3. Architecture and ownership

### 3.1 End-to-end data flow

```text
Plugin picker / node editor / optional Builder suggestion
                  |
                  v
     Graphlink validated WebSocket intent
         app-plugins.invokePluginIntent
                  |
             grant and schema check
                  |
          Pure frozen source snapshot
                  |
       Queue + bounded execution ticket
                  |
       Isolated Lean proof-checking job
        - offline/pinned mathlib root
        - parse/elaborate/compile
        - kernel checker
        - axiom/dependency policy auditor
                  |
     Structured result + bounded diagnostics
                  |
       Host result validation / stale check
                  |
    Undoable node-state update and scene publish
                  |
      UI status / diagnostics / proof receipt
```

**Trust boundary:** The sandbox's exit code and emitted diagnostics are data. The host must validate process identity, full bounded structured response, successful checker outcome, axiom auditor outcome, environment identity, and source binding before upgrading status. Human-facing process text never selects the verification status.

### 3.2 Proposed file map

```text
plugins/formal_lean/
  plugin.toml                      # SDK v1 declaration, scope and passive kind
  plugin.py                        # host-facing register(host), no Lean execution

graphlink_plugins/formal_lean/
  __init__.py
  registration.py                  # plugin handlers, schema and node creation
  contracts.py                     # request/response dataclasses, limits and versions
  node_state.py                    # immutable proof-state model + migrations
  policy.py                        # frozen statement/axiom/safety policy
  source_snapshot.py               # canonicalization, IDs, staleness
  proof_jobs.py                    # ticket lifecycle and async orchestration
  runner.py                        # sandbox orchestration, no shell interpolation
  receipt.py                       # canonical receipt and hash logic
  results.py                       # strictly validated output and classification

# Optional verified runner support kept outside the host, copied to image:
formal_lean_runner/
  README.md
  CheckProof.lean                  # reviewed Lean checker/audit helper source
  strict_audit.lean                # dependency/axiom audit implementation
  image/                           # reproducible Dockerfile + offline lock
  fixtures/                        # positive/negative Lean tests

web_ui/src/app/canvas/
  FormalLeanNodeView.tsx
  FormalLeanNodeView.test.tsx
  FormalLeanNodeView.css
  formalLeanScene.ts
  formalLeanState.ts
  formalLeanState.test.ts
  sceneStore.ts                    # narrowly typed client intent methods
  SceneCanvas.tsx                  # dedicated node renderer case
web_ui/e2e/
  formal-lean.spec.ts
  formal-lean-safety.spec.ts

backend/tests/
  test_formal_lean_plugin.py
  test_formal_lean_proof_jobs.py
  test_formal_lean_receipts.py
  test_formal_lean_security.py
  test_formal_lean_persistence.py
  test_formal_lean_environment.py

.github/workflows/
  formal-lean.yml                  # gated isolated live verification suite

docs/
  FORMAL_LEAN_USER_GUIDE.md
  FORMAL_LEAN_SECURITY.md
  FORMAL_LEAN_TOOLCHAIN_LOCK.md
```

The map is **proposed**, not present on `main`. Prefer extracting a small shared, tested process wrapper over duplicating Graphlink's existing resource guard/container logic. Do not make `backend/domain` depend on Lean, FastAPI, plugins, or process execution.

### 3.3 Plugin manifest sketch (proposal)

```toml
[plugin]
id = "formal_lean"
name = "Formal Mathematics (Lean 4)"
description = "Write, check, and retain Lean 4 mathematical proofs in a pinned environment."
version = "0.1.0"
sdk_api_version = 1
entry_point = "plugin:register"

[scopes]
grants = ["graph.read", "graph.mutate"]

[frontend]
view = "generic"

[persistence]
passive_node_kinds = ["proof"]
```

The shipped `plugin.py` is trusted Graphlink-authored orchestration code. **Do not mark its Python worker `out-of-process` and assume that makes user Lean safe.** Independently confine every Lean process. An out-of-process plugin worker may be revisited after its current parentless-spawn/state/intent RPC limitations are fully tested for this use case. No special `code.execute` permission is claimed in this manifest: that known scope belongs to the **tool** layer; the proposed “run proof” permission must be clearly surfaced in the plugin's settings and intent UI, and a capability/approval review must decide whether more than an install-time plugin grant is warranted.

## 4. Verification semantics: what the application may and may not claim

Formal verification is not a Boolean returned by a subprocess. The feature must make three independent claims, each supported by separate evidence:

1. **Statement identity:** this is the exact proposition the user selected and reviewed, including the definitions, type-class instances, imports, and elaboration environment that determine its meaning.
2. **Checker acceptance:** a proof of that proposition was accepted under an identified, pinned checking process and trust model.
3. **Assumption compliance:** the proof's transitive axioms and other trust extensions obey the user's declared policy; the audit was complete and its result is trustworthy for this Lean version.

An application MUST NOT conflate these claims. The source can prove `True` while the natural-language caption says “Goldbach's conjecture is true”; that is not a formal proof of the caption. An elaboration that quietly changes the theorem's target is not acceptable merely because its file compiles.

### 4.1 Assurance levels and public labels

| Level | Required checks | Permitted user-visible claim | NOT implied |
|---|---|---|---|
| `draft` | Validated and saved source only | “Draft — not checked” | Compilation, proof or correctness |
| `lean_checked` | Exact named declaration exists; Lean compile/elaboration/kernel accepts it; no errors; environment pinned | “Lean checked — further audit required” | Absence of `sorryAx`, independent verification, intended statement |
| `audited` | `lean_checked` **plus** complete tested axiom/dependency traversal; only explicitly allowed assumptions; immutable receipt; all freshness checks | “Verified by Lean — approved axioms” | Security against malicious source that subverts Lean internals, equivalence to an English claim |
| `independent` | `audited` **plus** independently replayed `.olean` with `lean4checker --fresh` in a clean verifier environment | “Independently rechecked” | Full defense against malicious declarations or semantic trickery |
| `paranoid` | Trusted challenge/solution separation; `lake comparator --paranoid` in a validated sandbox using independently written checkers; axiom policy and exact challenge match | “High-assurance proof checked” | Absolute proof of an informal claim, security against every implementation flaw |

These are **proposed policy terms**. Confirm the actual `lean4checker` and `comparator` invocation interfaces and capabilities against the selected Lean version before shipping them. The [Lean reference manual](https://lean-lang.org/doc/reference/latest/ValidatingProofs/) recommends `comparator` for potentially malicious proofs, including unreviewed AI-generated submissions. For AI/external proof material, **never offer a “high-assurance” or equivalent green status from ordinary compilation alone**. If the validated `comparator` path is unavailable, restrict the UI to its honest lower assurance level; do not silently substitute a weaker check.

**MVP release decision:** implement `audited` as the minimal positive verification status **only after** its axiom audit is independently regression-tested. Supply the `independent` and `paranoid` paths as mandatory deliverables before enabling unreviewed AI-generated proof submission in the application. If v1 is intended to accept arbitrary adversarial Lean source from day one, `paranoid` is a **v1 release blocker**, not a post-launch enhancement.

### 4.2 Trust boundaries that cannot be proved away

- Lean's kernel checks a **formal proposition relative to axioms and definitions**; it does not verify that the proposition captures the author's intended mathematics.
- Imported mathlib definitions and notation influence meaning. Imports must be pinned and inspection of the elaborated statement must be available.
- `Classical.choice`, `propext`, and `Quot.sound` are commonly accepted foundational axioms. Their presence must still be disclosed; they are not “zero axioms.”
- `sorryAx` and *every* unapproved custom axiom are fatal to `audited`/`independent`/`paranoid` regardless of the process exit code.
- Version-dependent native-evaluation axioms are a distinct trust extension. By default reject them at assurance levels `audited` and above until a dedicated policy and external-checker capability is reviewed. A ban on the text `native_decide` is insufficient; detect dependencies, not just syntax.
- Tactics and macros can execute code during elaboration; a proof-oriented interface does not make Lean a safe language to execute on the host.
- A stored SHA-256 is an integrity check, **not** a digital signature, proof certificate, or statement of third-party trust.

### 4.3 Strict axiom auditing and Lean issue #15226

At the reviewed date, [Lean issue #15226](https://github.com/leanprover/lean4/issues/15226) is open and describes under-reporting from `Lean.collectAxioms` and `#print axioms` in particular imported-inductive cases. This is not a report of a kernel bug; it specifically affects the **audit**. Therefore:

1. Never use a lone `#print axioms` result to authorize `audited` or a higher badge.
2. On the candidate pinned Lean version, implement or reuse a separately reviewed **from-scratch transitive constant dependency traversal** that does not trust a potentially stale/corrupt imported axiom-summary cache. The upstream issue includes a workaround based on `env.find?` and `env.setExporting false` for examining imported declarations; treat this as a research lead, **not** a pasted production implementation.
3. Carefully visit all relevant declaration forms (definitions, theorems, constructors, inductives, mutual blocks, recursors, projection types and imported bodies), traverse **both the proposition type and its proof/body dependencies** as appropriate, and guard cycles without treating “currently visiting” as “fully audited.”
4. Explicitly compare the audited dependency set with the allowlist of foundational axioms; classify unrecognized axioms as *unknown/rejected*. Do not use substring matching for allowed axiom names.
5. Maintain golden repros: the exact `S9`/`S9.mk` imported-inductive example from #15226; an imported `sorry`; a direct `sorry`; custom axioms; mutual inductives; generated native axioms; renaming and insertion-order variants; high-assurance comparator tests.
6. An audit exception, incomplete imported body, version mismatch, undetermined native axiom, or unexpected declaration form makes policy status **indeterminate**, and therefore **not verified**.
7. Revalidate this auditor whenever the Lean version changes, including patches. If upstream resolves #15226, do not remove the independent tests.

The audit implementation must be reviewed by a Lean specialist before any strict-status release. Prefer an upstream-maintained, demonstrably correct dependency checker when available; avoid inventing kernel internals in Python.

## 5. Data contracts and proof-node state

### 5.1 Authoritative node model

Propose one persistent node kind, `formal_lean.proof`, with immutable revisions. The backend is authoritative; TypeScript parses and displays the wire shape but cannot assign `verified` on its own.

| Field | Owner | Meaning / constraint |
|---|---|---|
| `schemaVersion` | Backend | Integer beginning at `1`; migrations explicit; reject unsupported future versions |
| `nodeKey` | Backend | Random stable node identity for provenance; not reused after copy-as-new |
| `statementSource` | User | Formal Lean statement/challenge body, reviewed before verifying |
| `solutionSource` | User | User-supplied Lean proof/module source; never treated as trusted because of UI origin |
| `targetName` | Backend/user | Stable fully qualified target theorem name, not a user-selected substring of diagnostic text |
| `imports` | Backend policy | Explicitly approved Lean/mathlib module list or trusted challenge imports |
| `proofMode` | Backend | `challenge_solution` for high assurance; any simpler mode explicitly labeled weaker |
| `sourceRevision` | Backend | Monotonic revision/UUID, changed for every edit that could alter meaning |
| `lastReceipt` | Backend | Immutable, validated receipt reference; can be stale or historically verified |
| `history` | Backend | Capped list of prior receipt references, not a large inline transcript |
| `displayNotes` | User | Informal caption only, never part of the verified formal proposition |
| `editorDraft` | Frontend | Unsaved editing buffer; must not be mistaken for the last checked source |

**Important design choice:** for the high-assurance path use a **trusted challenge module** and a separate **candidate solution**. The user approves the exact challenge text and imports; the candidate solution cannot supply a replacement challenge or quietly redefine its notation. Follow the pinned version's `comparator` challenge/solution contract. For ordinary learning workflows, a single editable Lean source is useful, but it must not be silently promoted to challenge-equivalent assurance.

Source bytes use UTF-8. Normalize only under a documented versioned normalization policy (e.g., LF newlines) **before** hashing and writing the sealed input; do not hash one representation and check another. For visible diagnostics retain line/column offsets for the exact bytes Lean receives. Reject embedded NUL, undecodable bytes, unexpected source path types, and oversized payloads at the boundary. No user-supplied filesystem path is accepted as an output path.

### 5.2 Wire shape (illustrative, not generated-code claim)

The existing `SceneNodeRow.pluginState` is `dict[str, str]`; Graphlink's graph serialization coerces plugin fields to strings. Retain that contract for v1, with one JSON-encoded field named `formalLean`. The parser and schema below are **proposals**:

```json
{
  "formalLean": "{\"schemaVersion\":1,\"nodeKey\":\"...\",\"sourceRevision\":\"...\",\"displayStatus\":\"draft\",\"receiptId\":null,\"currentAttemptId\":null}"
}
```

- In `serialize(node)`, return `{"formalLean": compact_json_string}`; never return nested objects directly to `pluginState`, since the current contract stringifies them.
- In `serialize_saved(node)`, store validated native JSON, *not* the same inflated live UI payload. Use `externalize_state=True` for bounded, content-addressed data where appropriate. Graphlink's plugin-state maximum is **1 MiB** in the inspected SDK; large proofs/transcripts should be separate verified assets with explicit retention policy rather than huge plugin-state blobs.
- The wire serializer must never throw on damaged state; it should fail to a stable error/disclosure. **The disk restore must preserve unreadable raw data for recovery** rather than replacing it with an empty “verified” state.
- Avoid a new core `SceneNodeRow` field until the existing plugin wire's size/performance is measured and shown to be insufficient. If adding a field becomes necessary, edit the Python contract and run `python contracts/codegen.py --write`; do not cast around generated TS types.
- The UI must fetch heavy, paginated diagnostics/source assets via a scoped read path rather than embedding them in every scene snapshot.

### 5.3 Frozen verification request and response protocol

Illustrative envelope for the host-to-runner boundary:

```json
{
  "protocolVersion": 1,
  "jobId": "unique-host-issued-id",
  "sourceHash": "sha256-of-canonical-frozen-request",
  "challengeHash": "sha256-of-trusted-challenge-bytes",
  "environmentId": "pinned-lean-and-mathlib-environment-id",
  "policyId": "strict-policy-version-id",
  "targetName": "Graphlink.Target",
  "source": "exact-solution-bytes-or-sealed-file-reference",
  "limits": {"wallSeconds": 120, "memoryMiB": 4096, "maxLogBytes": 262144}
}
```

The runner response must be **structured, size-bounded and schema-validated** and include: its echoed job/source/challenge/environment/policy identity, actual checker versions, exit statuses for individual stages, target theorem existence, exact theorem type or a canonicalized representation, axiom/dependency report, diagnostics with source spans, and a bounded duration. **No `verified: true` flag from the untrusted Lean process is authoritative.** A host-side verifier reconstructs the eligibility result from all required checked evidence. Use distinct fields for “Lean compilation completed,” “kernel/checker accepted,” “independent replay completed,” “axiom audit completed,” and “challenge equality established.”

### 5.4 Immutable receipt v1

A `VerificationReceipt` should include at least:

```text
schemaVersion; receiptId; jobId; sourceRevision; nodeKey
statementHash; challengeHash; solutionHash; targetName
elaboratedTargetRepresentationHash; elaboratedTargetDisplay
leanToolchainIdentity; leanBinaryDigest; mathlibGitCommit
lakeManifestDigest; trustedImportsDigest; imageDigest; checkerDigests
policyId; allowedAxioms; observedAxioms; unclassifiedAxioms
compilationOutcome; kernelOutcome; lean4checkerOutcome; comparatorOutcome
axiomAuditOutcome; challengeMatchOutcome; effectiveAssuranceLevel
resourceLimits; deadlineReached; cancellationReason; diagnosticAssetRef
createdAtUtc; totalDurationMs; hostOrchestratorVersion; receiptDigest
```

`receiptId` identifies a single completed attempt, **not** a reusable certificate of an edited proof. Canonicalize with a reviewed deterministic JSON encoding (stable key order, byte-preserving strings, no NaN/Infinity, explicit types) and compute a SHA-256 digest over all fields excluding `receiptDigest`. Use the existing content-addressed asset store where appropriate, but do not assume every stored receipt is trustworthy: after import, verify every referenced asset and reject missing/mismatching dependencies. In a strong-trust threat model, independently recheck receipts in the pinned environment; hashes alone do not establish authenticity.

### 5.5 Status state machine

The **attempt status** and **receipt validity** are orthogonal; model them separately:

```text
Draft -> Queued -> Checking -> (Completed | Failed | Timed out | Cancelled | Unavailable)
                                  |
                                  v
                         Policy evaluation
                 (Audited | Rejected | Indeterminate)
                                  |
                                  v
                          Receipt committed
```

- Only completed, fully audited attempts can yield `audited`/higher.
- `Failed` is not “false theorem”: it includes syntax, tactic, type mismatch and incomplete proof diagnostics.
- A stale receipt is still retained as *historical evidence*, but **not** displayed as verification of current text.
- The persisted node never stores an ephemeral `Checking` state as if it were durable. On restart a previously running attempt becomes `Interrupted — recheck required`.
- Cancellation and timeouts do **not** erase the most recent immutable receipt. They may update last-attempt diagnostics, not the result being verified.
- Grant disabled, dependency absent, or image unavailable means “verification unavailable,” not “failed proof.”

### 5.6 Idempotency and optimistic concurrency

`CheckProof` takes `nodeId`, `sourceRevision`, `expectedSourceHash`, `attemptId`, and explicitly selected assurance mode. Repeating the **same** `attemptId` with identical fields must resolve to the same running/completed attempt; reusing it for different inputs is a conflict. Before committing after any `await`, compare the live document identity (chat/workspace), node object identity, current source revision/hash, target/import policy, grant state, and relevant linked-source hashes against the frozen snapshot. If any changed: mark the result orphaned/stale and **do not call `record_command` or publish it as the current proof**. Retain bounded diagnostics for audit if permitted by the user's retention setting.

## 6. Lean and mathlib environment reproducibility

### 6.1 Pinning policy

Use an exact Lean 4 toolchain and compatible mathlib4 Git SHA as one **versioned environment profile**; never pair “latest Lean” with an arbitrary mathlib checkout. The inspected upstream mathlib master `lean-toolchain` contained `leanprover/lean4:v4.37.0-rc1` on 2026-11-23, but **do not treat this floating master/RC as the release target**. Choose a specific tested stable tag or commit at implementation time. Capture its exact toolchain bytes, `lake-manifest.json` SHA-256, dependencies' commits, Lean executable digest, and environment image digest. Every update is a deliberate reviewed profile migration.

Provision via a separate explicit installer/build step:

1. Maintainer chooses tested `lean-toolchain` + mathlib revision and commits their lock manifest.
2. Build a trusted environment image with immutable dependency revisions; verify source integrity and provenance.
3. Fetch mathlib compiled caches **only while provisioning**, verify/cache them, and record their identities. As documented by [mathlib4](https://github.com/leanprover-community/mathlib4), `lake exe cache get` downloads compiled artifacts; it must **never** execute implicitly during an end-user check.
4. Prebuild trusted imports and verified helper binaries; run a reproducibility/compatibility test suite in CI.
5. Publish immutable image digests and platform support in `docs/FORMAL_LEAN_TOOLCHAIN_LOCK.md`.
6. At runtime, start with `--pull=never` or an equivalent no-network/no-auto-download posture. Missing toolchain is a recoverable, explicit `environment_unavailable` error, never a silent host-Lean fallback.

Do not ship Lean/mathlib as mandatory Python dependencies. Graphlink's base `pyproject.toml` and Python 3.12 lock should remain unchanged unless the small host orchestration layer genuinely needs a new reviewed dependency. A container engine/toolchain is an optional, explicitly disclosed prerequisite.

### 6.2 Sealed module and import model

- The trusted challenge, imported libraries, checker helper and compiled artifacts are **read-only** to untrusted proof elaboration.
- Use an allowlist of approved import modules resolved **only** from the pinned environment; never let a submitted `import Foo` resolve to a user-written `Foo.lean` shadowing a trusted library.
- The candidate may author its own proof-local names, but cannot redefine the trusted challenge or modify package configuration and the trusted import path. If a broader module feature is added later, define an isolated namespace and an explicit module graph.
- Do not honor a candidate's `lakefile`, `lean-toolchain`, `lake-manifest.json`, `LEAN_PATH`, environment variables, or absolute import paths. Runner sets its own fixed environment.
- Verify output `.olean`/proof artifacts in a **separate verifier environment**; do not trust a file that adversarial code could overwrite after verification.
- If the chosen version's checker needs writable caches, put them only in bounded ephemeral directories with no host workspaces or secrets mounted.

### 6.3 Platform and upgrade matrix

| Host | MVP expectation | Mandatory proof of support |
|---|---|---|
| Windows 11 + Docker Desktop/compatible engine | First-class: Graphlink's current CI is Windows-sensitive | Real process-tree cancellation, mount/path normalization, cleanup, long Unicode filenames, cold image check |
| Ubuntu Linux + Docker Engine/Podman | First-class: production wheel/CI also targets Ubuntu | Non-root execution, UID/GID, read-only mounts, offline/no fallback, seccomp/resource behavior |
| macOS | Supported only after native E2E validation | Engine detection, filesystem/volume security, Apple Silicon toolchain/image and checker compatibility |
| No supported isolation engine | Feature disabled with setup instructions | **Zero** Lean subprocesses spawned on the host; no host fallback |

Do not interpret availability of a Docker command-line binary as proof the daemon exists, the image is pinned, or the isolation profile is functioning.


## 7. Proof-checking pipeline and checker protocol

### 7.1 Two-file challenge/solution design

For a high-assurance proof, establish two logically separate input files:

- **Challenge**: a trusted, canonicalized theorem statement and its trusted import/definition closure. This is the *claim being checked*. It must be authored/approved outside the candidate proof submission and shown to the user before check. The challenge's placeholder proof follows the selected `comparator` protocol; do **not** treat its placeholder `sorry` as a completed theorem.
- **Solution**: untrusted Lean commands/tactics/proof terms attempting to solve that challenge. Compiled only within restricted execution and evaluated against the trusted challenge; the solution cannot change the challenge statement.

The Lean reference manual's [Validating a Lean Proof](https://lean-lang.org/doc/reference/latest/ValidatingProofs/) describes this challenge/solution separation and `lake comparator --paranoid`. Its actual input conventions may evolve. The **first implementation spike** must pin a release, compile minimal challenge/solution fixtures, demonstrate a successful comparison, demonstrate an altered statement rejection, and document exact invocations. Do not invent an ad hoc text comparison as a substitute for the checker.

For an educational “single-file” mode, the Lean file may be compiled in isolation and display diagnostics, but its assured statement must be the **fully elaborated, displayed target** rather than an informal heading. If it is not compared to a separate immutable challenge, that absence must remain visible in its assurance label.

### 7.2 Required verification steps (ordered)

1. **Authorize.** The plugin is enabled, the user has explicitly initiated checking and approved the current execution policy, and this request has the permitted assurance mode. The initial product must not allow Builder/agent tools to run `check` merely because the plugin is granted.
2. **Freeze.** On the backend event-loop thread, snapshot the source bytes, target, imports, policy ID, relevant linked nodes, chat/workspace identity and node revision. Check size/schema and create an opaque attempt ID. Do not allow a worker to read a live `SceneDocument`.
3. **Verify environment.** Confirm the exact pinned profile, image digest, compatible Lean/mathlib imports, checker executable set, sandbox policy, and working daemon; an unrecognized image or missing independent checker fails the requested assurance level.
4. **Materialize.** Write only the frozen input files into a per-job private directory with restrictive permissions. Materialize the challenge separately from the untrusted solution. Use fixed names; no user-provided destination paths. Ensure temporary materialization is not inside the Graphlink repo, chat DB directory, home directory, or another project.
5. **Compile untrusted source.** Launch a fixed, argv-based, offline Lean build/check command inside the sandbox. Never use `shell=True` for user data. `lake update`, network fetching, plugin installation, and user-supplied script invocation are forbidden. Capture complete exit status plus size-bounded diagnostic streams.
6. **Confirm the target.** A trusted helper (run in the right phase/environment) establishes that the fully-qualified theorem exists, has the expected elaborated proposition and does not merely share the same name as a different declaration. A file with an unrelated successful theorem must be rejected.
7. **Recheck proof.** For `independent`, replay output with `lean4checker --fresh` in a fresh verifier context. For `paranoid`, run the supported comparator with the trusted challenge, verify the tool actually performed all required checker stages and record which independent kernels were enabled. No stage may silently skip due to a missing binary.
8. **Audit assumptions.** Traverse transitive axioms independently of the potentially affected `collectAxioms` cache. Reject `sorryAx`, unapproved axioms, and any trust extension forbidden by the selected level. Unknown and partial results are indeterminate, never successful.
9. **Validate artifacts.** The host verifies returned structured evidence against the frozen request and protected environment identity. It does not parse “success” out of untrusted stdout and does not treat the solution's own assertions as a certificate.
10. **Recheck liveness.** After every asynchronous boundary, verify grant, node identity, revision, policy, graph, expected linked sources, and any user cancellation. Reject a stale response without mutating the graph.
11. **Commit once.** On eligible completion, atomically persist a new immutable receipt and update the node's current result through `SceneDocument.record_command`; publish one `scene` update. Retain the previous receipt as history and mark other dependent outputs stale if required.
12. **Cleanup.** Terminate/reap all job processes/containers, remove ephemeral source directories, close descriptors, release capacity slots and prune orphaned temporary artifacts regardless of success or failure.

### 7.3 Positive proof fixture (illustrative Lean source)

The exact imports, namespace, and helper invocation need a tested pinned version. This mathematical example is a **fixture idea**, not a claim that this document ran a Lean compiler:

```lean
import Mathlib

namespace GraphlinkChallenge

theorem add_zero_nat (n : Nat) : n + 0 = n := by
  simp

end GraphlinkChallenge
```

A negative fixture should have the same target statement with `by sorry`; another should attempt to prove `1 = 2` using a newly introduced axiom; another should change the statement to `True` while preserving the theorem name. All must be rejected at the appropriate assurance level even if a naive `lean` process can complete.

### 7.4 Minimal trusted verifier responsibilities

Make the verifier as small as possible. A dedicated, pinned Lean-side program should:

- Read a fixed-format request/manifest supplied by the trusted host; never execute a user-selected program name.
- Resolve the target by **fully qualified exact name** and report its declaration kind, elaborated type and relevant environment identities.
- Produce the dependency/axiom report using the independently tested algorithm.
- Distinguish “declaration not found,” “not a theorem,” “unexpected type,” “unchecked or incomplete proof,” “axioms rejected,” “checker failed,” and “unsupported version.”
- Emit a single versioned structured result through a fixed output file/pipe under a strict byte limit. Do not derive status from source comments, `#eval` output, trace strings, or regexes on compiler messages.
- Write no credentials, secret environment fields, or arbitrary filesystem paths into receipts.

Proof artifacts from an untrusted sandbox must not be executed outside it as arbitrary code. If the independent verifier parses untrusted `.olean` data (as `lean4checker` does), treat that parser as exposed to hostile binary input and place it under an additional restrictive verifier sandbox. The manual expressly notes that `.olean` parsing can have risks.

### 7.5 Imports, theorem scope and target meaning

For v1, prefer a trusted fixed package namespace and one named theorem per proof node. Support theorem parameters and standard dependent types, but reject a missing target and ambiguous/redefined target names. The meaning of a statement may depend on implicit arguments, scopes, instances, notation and coercions: the receipt must identify the exact environment and expose an elaborated target inspection view (safely rendered). A proof of a weaker theorem is not an alternative way to satisfy the challenge. An AI-generated natural-language restatement never overrides the actual theorem.

A target may be parameterized (`theorem T (n : Nat) : ...`) and need not be a closed Boolean. Do not reduce proof identity to sampled test cases or numerical equality comparisons. No symbolic/numeric manipulation library is treated as a formal authority unless a Lean proof term is checked for the actual statement.

## 8. Host-security and isolation threat model

### 8.1 Adversary and exposed assets

Treat both user-typed and AI-generated Lean files as potentially malicious, even if the application is local-first. Plausible adversaries include a malicious imported source, compromised cached artifacts, an adversarial proof pasted into a shared workspace, prompt-injected model output, a forged saved receipt, and a denial-of-service proof tactic.

Protect: API credentials, `~/.graphlink` chat/knowledge files, arbitrary home/project files, host processes, other proof jobs, other workspace graphs, network connectivity, the backend's authenticated WS/REST routes, installed toolchain integrity, and the distinction between an unverified proof and a verified one.

### 8.2 Mandatory execution boundary

The existing [`backend/plugin_worker.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/backend/plugin_worker.py) and [`backend/plugin_sdk.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/backend/plugin_sdk.py) explicitly state that the plugin worker isolates host memory/secrets but **does not sandbox worker filesystem or network access**. Graphlink separately has a workspace-agent Docker/Podman `isolated` profile, with no host fallback. Borrow that fail-closed pattern and build a Lean-specific runner with at least:

- Immutable image identity, **no pull during run**, no uncontrolled package resolution, no Docker socket or daemon credentials mounted into the container.
- `--network=none` or equivalent; assert offline behavior in tests (including DNS/external-address attempts). No “Allow network once” for the proof checker itself.
- Non-root UID, dropped capabilities, no-new-privileges, read-only image filesystem, minimal seccomp/AppArmor/OS security profile where supported. Rootless containers are preferred, but real platform limits must be tested.
- Exactly scoped, read-only trusted toolchain/compiled package mounts and read-only per-job input; one bounded, private writable output/tmpfs mount if needed.
- Explicit CPU, memory, process-count, file-size, disk/tmpfs, wall-clock and stdout/stderr caps. Suggested first **benchmark candidates**, not proven adequate: 1 CPU, 4096 MiB RAM, 32 processes, 120 s wall-clock, 256 KiB captured diagnostics, 1 MiB source, 1 MiB verifier JSON. Tune based on real mathlib cold/warm tests without bypassing load-bearing CI budgets.
- A generated opaque job/container name/ID owned by Graphlink; kill-and-remove by exact ID on completion, timeout, cancellation, process crash and launch failure. Do **not** rely on killing only the Docker/Podman CLI: the container daemon may keep the workload alive.
- A validated host/container mount allowlist; no parent directory of a protected path, no host repo mounting, no symlink escape, no arbitrary absolute paths, no mounted network volumes without explicit security review.
- A scrubbed, fixed environment (reusing `safe_subprocess_env` principles) with no inherited provider keys, proxies, user `PATH`, `LEAN_PATH`, or host-specific variables that can redirect tool execution.

If a given platform cannot enforce the required restrictions, reject the run as `isolation_unavailable`. Host resource guards (`graphlink_execution_guard.py`) may contain the *Docker CLI* but are not a substitute for the workload's container policy. They should still be used for consistent process lifecycle where appropriate.

### 8.3 Threat/control/test mapping

| Threat | Primary control | Mandatory attack-oriented test |
|---|---|---|
| Lean macro/tactic reads `~/.graphlink/session.dat` or environment secrets | No home mount; sanitized environment; offline non-root sandbox | Canary secrets inaccessible and absent from logs/results |
| Tactic attempts `curl`/sockets/exfiltration | Offline networking; no gateway escape | Container cannot contact public IP, host loopback, or backend port |
| Memory bomb / infinite elaboration | Memory/process/CPU/wall caps; forced cleanup | Limits reached; entire container exits; UI responsive |
| Fork bomb / spawned child survives cancel | PID cap; container-ID kill/remove | No orphan workload after cancel or host disconnect |
| Candidate modifies challenge, trusted `.olean`, manifest or checker | Read-only trusted mounts; challenge/solution separation; independent verifier | Attempted writes fail; mismatch rejected |
| Candidate supplies custom `lakefile` or `LEAN_PATH` | Ignore user package config; fixed invocation/environment | Wrong dependency not loaded; no network download |
| Candidate forges “verified” in stdout/JSON | Host-side independent evidence and identity validation | Forged success message remains unverified |
| Imported `sorryAx` or custom axiom | Complete transitive axiom audit, not text grep | Axiom in imported dependency is rejected |
| Candidate deliberately bypasses Lean checking (`debug.skipKernelTC`-style) | `lean4checker` / comparator in separate verifier sandbox | Checker rejects untrusted theorem artifacts |
| Imported-inductive axiom-cache bug | Independent traversal and #15226 golden suite | Reproducer detects axiom every time |
| Edited node / grant revoked while job runs | Snapshot, CAS-style commit checks, cancellation | No stale result committed or falsely shown current |
| Invalid asset or corrupt saved receipt | Content hashes, strict schema validation, fail-closed restore | Missing/tampered receipt not marked verified |
| Malicious markup in compiler diagnostics | Render plain text; no HTML interpretation | No script execution/unsafe links in UI |
| Cross-workspace leak | Per-job/private directory, scope-check every action | No access to another chat/workspace node or asset |

### 8.4 Resource budgeting and degraded behavior

Enforce global queue capacity and per-node single-flight semantics. A proposed default is **one active Lean job per Graphlink process** and **one queued job per node**, configurable downward when the host has insufficient memory. Queued jobs receive a visible queue state and can be cancelled. No launch occurs if the host cannot enforce the configured budget. A bounded timeout is not a theorem failure and must be represented separately. Admission control must account for verifier-stage memory and CPU needs, not just source elaboration.

### 8.5 Privacy and retention

A local proof can contain unpublished mathematics. By default, perform checking offline and do not send source, theorem statements or diagnostics to any LLM provider, external telemetry, or web search. If future AI assistance is enabled, use existing Graphlink model-provider consent and show exactly what proof text leaves the device. Delete temporary jobs on completion; limit persistent diagnostics and receipts; respect existing workspace export and deletion semantics. Document whether exported workspaces include full proof sources and how an end user can inspect/remove them. Treat saved receipt metadata as potentially sensitive even if it contains only theorem identifiers.

## 9. Graphlink backend implementation contract

### 9.1 Registration through the current public SDK

Use the existing pattern from the `science_*` plugins and the SDK's reference plugins. The illustrative registration shape (not runnable code) is:

```python
# plugins/formal_lean/plugin.py (design-only outline)
from graphlink_plugins.formal_lean.registration import register_formal_lean


def register(host):
    register_formal_lean(host)

# registration.py (design-only outline)
# host.register_node_kind("proof", create_seed, requires_parent=False,
#                         serialize=serialize_live,
#                         serialize_saved=serialize_disk,
#                         deserialize=deserialize_disk,
#                         externalize_state=True)
# host.register_picker_entry(node_kind="proof",
#                            name="Formal Mathematics (Lean 4)",
#                            category="Validation & Delivery", ...)
# host.register_intent("save_source", save_source, args_schema=SaveSourceArgs,
#                      expose_to_builder=False)
# host.register_intent("check", start_check, args_schema=CheckArgs,
#                      expose_to_builder=False)
# host.register_intent("status", get_status, args_schema=StatusArgs,
#                      expose_to_builder=False)
# host.register_intent("cancel", cancel_check, args_schema=CancelArgs,
#                      expose_to_builder=False)
# host.register_intent("export_receipt", export_receipt,
#                      args_schema=ExportReceiptArgs, expose_to_builder=False)
```

Do not register dynamic plugin-specific `(topic, intent)` strings through `SessionBus.register_intent`: Graphlink's [`tests/test_undo_classification_gate.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/tests/test_undo_classification_gate.py) requires statically enumerated bus intent registrations. Use the existing literal `app-plugins/invokePluginIntent` chokepoint and classify the **plugin's own** mutations in focused tests/reviews. `backend/plugins.py` grants gate calls before invoking the handler; **additional check-time authorization and the sandbox boundary are this plugin's responsibility.**

The current out-of-process plugin worker reconstructs node registrations with `requires_parent=True`, so do not assume that setting `[runtime].isolation = "out-of-process"` will preserve parentless proof-node creation. Keep the small, trusted Graphlink-owned plugin adapter in-process in v1 and run untrusted Lean **separately** in its container. Revisit this split only after a tested SDK capability migration.

### 9.2 Graph mutation rules

- **Creating** a proof node uses the SDK's factory/`PluginNodeSeed` and host `record_command` path, preserving Graphlink's undo model. A parentless proof node must spawn using the frontend-provided viewport center, as existing SDK nodes do.
- **Editing** source/statement/notes is an explicit undoable `document.record_command`; increment `sourceRevision` and mark the previous proof stale in the same atomic edit. Do not run Lean while the user types.
- **Checking** performs no graph mutation until the result is known; ephemeral progress lives in a bounded session job ledger. A completed accepted result is committed through `record_command` once (or an explicitly reviewed non-undoable receipt-history mechanism; v1 preference: undoable node result mutation).
- **Cancelling/status** do not rewrite the theorem or undo a previous verification. Cancellation should be idempotent, observable and scoped to the correct node/workspace/attempt.
- **Exports** should return a validated receipt/asset reference through an existing safe download mechanism; prohibit arbitrary target filenames or path traversal.
- **Plugin grant revocation** cancels active jobs, prevents new checks, and preserves inert saved node state. The saved badge must no longer imply a fresh live check is possible.
- **Missing plugin** must not erase the original namespaced node or receipt in storage; rely on existing passive-kind compatibility and test save/reopen/re-enable explicitly.

Do not introduce new `backend/domain` imports of `graphlink_plugins`, Lean, FastAPI or event buses. Pure state dataclasses/serializers belong to the plugin domain; process runners and bus notifications belong to its outer service layer.

### 9.3 Persistence implementation notes

Use the SDK's `serialize_saved` / `deserialize` and `externalize_state` seams. Validate a prior payload's version, identity, string lengths and asset references **before** using it to display proof status; preserve unsupported states in an inert form. Graphlink's session save/load includes generic namespaced plugin-node persistence and content-addressed state assets: exploit those features rather than adding a parallel `~/.graphlink/lean.db` that can lose linkage or permission controls. A separate cache of trusted **toolchain artifacts** is acceptable but must never be confused with per-workspace proof receipts.

The frontend should show a historic receipt after reopening, but the strongest **current-session claim** must require the configured recheck policy. Proposed UX: “Previously verified on [recorded environment] · not rechecked in this session” until a fresh or validated replay check completes. Distinguish stale due to source changes from not rechecked because the environment is currently unavailable.

### 9.4 Intents, explicit approval and model exposure

Graphlink's plugin intent dispatcher has grant checks, but a plugin grant is **not equivalent to a one-time permission to run arbitrary user code**. Require a visible execution disclosure in the Lean editor, at least on first check per session/policy profile, with a non-surprising `Check proof` button. Do not add new scope strings that are not in `backend.tools.KNOWN_SCOPES`. An optional future integration with the ToolRegistry's `code.execute` approval semantics needs a separate reviewed design; do not silently change the SDK or enable Builder tools to gain it.

`expose_to_builder=False` on all proof execution and source-editing intents for MVP. Any later model-origin proof proposal should write to a **draft**, visibly mark it untrusted, and require a human check/approval under the same sealed verifier path. No LLM output directly writes the trusted challenge.

### 9.5 Async job ownership and lifecycle

Use a testable `ProofJobManager` with per-document/per-node attempt identity and explicit cancellation tokens. The handler may use `PluginRunContext.run_background_scene_job` only after confirming its exact semantics fit cancellation and result-commit needs; Graphlink already uses this for scientific background attempts. Run container operations off the FastAPI event loop (e.g., `asyncio.to_thread`/subprocess orchestration), **never** inside a synchronous `record_command` mutator. Re-check liveness after all `await`s. Close processes/containers on app shutdown and after reload, plugin revocation, proof deletion, graph switch and cancellation. Repeated stop must not raise or report success while the container still exists.

## 10. Frontend and editor specification

### 10.1 Canvas rendering and contract seam

The current `SceneCanvas.tsx` recognizes `science_*.workbench` specially and otherwise shows a basic placeholder for unknown node kinds. A standalone formal plugin would otherwise appear as a mostly unusable title card. Add:

- `isFormalLeanKind(kind): kind is "formal_lean.proof"` with exact comparison (not broad regex that could select a different plugin).
- A stable `FormalLeanFlowNode` mapping with `pluginState.formalLean` read from validated `SceneNodeRow`. Preserve React Flow memoization and stable store function identities.
- `FormalLeanNodeView` (a compact canvas card) and a focused modal/panel editor for editing extensive Lean source. The canvas shows only target summary, assurance level, last-check identity/freshness, and a prominent **Open editor** action.
- Narrowly typed `SceneStore` methods calling `transport.request("app-plugins", "invokePluginIntent", ["formal_lean", action, args])`; distinguish retryable reads from edits. Avoid unclassified/queueable toggles. Use the current transport's request lifecycle and error feedback.
- Integration with `PluginPicker` using its existing data-driven entry; no hardcoded plugin selector. The default grouping proposed is **Validation & Delivery**, whose category is already registered in `backend/plugins.py`. Do not add an unnecessary category.

### 10.2 Focused proof editor layout

**Header:** exact target theorem name; formal statement label; linked challenge/environment badge text (not a color-only trust signal); source revision.

**Main editor:** source entry with Lean syntax highlighting if a compatible local editor library is available and reviewed; otherwise an accessible kit `TextArea` first. Separate *Challenge/Statement* (explicitly approved) and *Solution/Proof* tabs in high-assurance mode. The toolchain/import profile is shown but not editable by arbitrary source injection.

**Actions:** `Save draft`, `Check proof`, `Cancel check`, `Inspect elaborated target`, `View assumptions`, `Export source`, `Export receipt`, `Recheck previous receipt`. Prevent duplicate accidental concurrent checks but preserve explicit retry after timeout/error.

**Diagnostics:** ordered list of error/warning messages with file/line/column and clickable editor navigation. Display bounded error output as text; escape all source and compiler content. Make “Failed to compile”, “Proof incomplete”, “Unknown axiom”, “Timeout”, and “Missing environment” distinct.

**Proof result:** a statement preview *separate from* the informal explanation; permitted/observed axioms; assurance tier; toolchain/mathlib revisions; checker stages; receipt hash; historical-versus-current status. No generic “✓ Proven” for `lean_checked`, `indeterminate`, or stale receipts.

### 10.3 Design-system and accessibility rules

Follow [`AGENTS.md`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/AGENTS.md) and [`docs/DESIGN_SYSTEM.md`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/docs/DESIGN_SYSTEM.md): use the actual UI kit (`Button`, `TextArea`, `Tabs`, `Dialog`, `StatusPill`, `InlineAlert`, etc.), `--gl-*` tokens, sentence case and ellipsis `…`; no raw native selects/checkboxes or browser `window.confirm`. Keep keyboard focus visible, diagnostic navigation screen-reader-accessible, status updates in appropriate live regions, and colored status pills paired with plain text. Support both themes, zoom levels, and Windows screen readers. Treat browser syntax-highlighter dependencies as a new supply-chain and performance surface subject to npm audit.

### 10.4 Revision and stale-state interaction

Unsaved editor text is **never** verified. If a user modifies a previously checked solution, immediately show “Unsaved changes — last check applies to an older revision”; on save, mark the previous receipt stale. If the user edits the trusted challenge, its digest changes and any solution proof is stale even if unchanged. If an upstream graph node is bound to the challenge, any content/source revision change must invalidate the downstream proof receipt until rechecked. A saved historic success must remain inspectable but cannot be rendered as applying to the new target.

## 11. Graph composition and interoperability

### 11.1 MVP edges

A proof node may be parentless or connected as a branch from a note/chat/document node, but **graph edges are not automatically formal logical entailment**. An upstream chat summary must never be silently injected into a Lean statement, imported as trusted code, or interpreted as a proven premise. Source acquisition from a graph node is an explicit snapshot action with source node ID, revision, content hash, reviewed mapping into the challenge, and refreshed provenance.

### 11.2 Connection with Mathematics and Scientific Workbench

- Keep `science_mathematics` and other numerical/scientific packs unchanged. Their receipts are **computational** evidence, not Lean proof certificates.
- Future mathematical bridges can generate formal *conjectures* from scientific input, but the bridge must define exact number types (e.g., `Nat`, `Int`, `Rat`, `Real`) and assumptions (measurement error, finite precision, units). A float result cannot simply be treated as a theorem about real numbers.
- Any future “proof from data” integration must supply a formal theorem about a verified model of those data, not silently assert unproved measurement assumptions.
- A formal proof may be cited in chat as a source with the exact theorem statement and check level. A model must not upgrade it from “Lean checked” to “proved” in generated prose.

### 11.3 Graph Runner and agent integration (later phase)

The existing Graph Runner has explicit supported kinds and run semantics; it will not automatically execute a new `formal_lean.proof` kind. Leave it outside automatic graph runs for MVP. A later runner adapter must include preview-before-run, dependency ordering, source/receipt hashes, explicit human approval, failure/cancellation semantics, limited concurrency, and a hard prohibition on propagating a stale or non-compliant proof as validated input. Preserve Graph Runner's current classification and its tests before enabling the new kind.


## 12. Deterministic caching, performance, observability and failure handling

### 12.1 Two separate caches

**Trusted environment cache:** pinned Lean/mathlib image and prebuilt trusted imports, keyed by toolchain manifest + image digest + checker version. Provisioning is explicit. A cache miss **never** triggers an unapproved network fetch during a proof check.

**Proof receipt cache:** previous successful proof attempts, keyed by canonical challenge/source hashes, target name, resolved imports, exact environment ID, checker/policy revisions and assurance level. It may avoid recomputing a display of *historical evidence* but must **not** let a lower assurance level satisfy a stronger request. A cache hit from a workspace import, external file or another user's machine does not count as a fresh verified result until authenticity and replay requirements are satisfied.

The cache key must account for **every** imported declaration that affects meaning, the trusted challenge, external checker identity and the axiom-policy implementation. A content hash of the source alone is inadequate. Prevent cross-session/cross-workspace leakage in cache lookup and export.

### 12.2 Incremental editing versus explicit checking

Do not launch a full mathlib build for every keystroke. MVP uses Save / Check buttons. Later, Lean LSP can provide goal previews and errors, but it is another long-lived untrusted execution surface: it needs the same isolated process, scope, resource, lifecycle and import guarantees, and its interactive goals are **not** a substitute for completed proof verification. Make UI preview data visually distinct from final receipts.

### 12.3 Service-level objectives (initial budgets; validate empirically)

| Metric | First engineering target | Measurement method |
|---|---|---|
| WS/event-loop stall attributable to scheduling or finishing a proof | Respect Graphlink's existing ≤100 ms performance-test limit; aim substantially lower | Existing `backend/tests/perf` watchdog + proof scheduling workload |
| Idle proof-node render cost | Preserve current React render-count ratchets | `renderCountGate.test.tsx` extensions, 10-/500-node scenes |
| Queued job admission | Bounded and nonblocking; observable within next scene publish | Fake controlled runner in unit/integration test |
| Cancellation | Stop whole container tree promptly and show confirmed stopped state | Platform E2E measuring cleanup; threshold selected from observed baseline |
| Warm simple theorem | Baseline measured per pinned profile, no invented universal seconds figure | CI metric and optional performance regression alert |
| First trusted environment provisioning | Separate action; progress and disk requirements disclosed | OS/platform installation E2E |
| High-assurance comparator | Explicitly benchmarked separate latency and memory budget | End-to-end comparator fixtures, not compared to mere compile time |

Avoid overpromising fixed latency across Mathlib versions and machines. Log **timings and stage identity, not proof contents by default**.

### 12.4 Structured, privacy-preserving log events

Event categories: `proof.requested`, `proof.admitted`, `proof.environment_checked`, `proof.sandbox_started`, `proof.lean_completed`, `proof.replay_completed`, `proof.axiom_audit_completed`, `proof.result_discarded_stale`, `proof.receipt_committed`, `proof.cancelled`, `proof.cleanup_failed`.

Safe fields: opaque attempt ID, declared assurance level, environment ID/hash, coarse durations, normalized outcome and error category. Never log API credentials, entire Lean source, hidden home paths, unredacted container environment, raw traceback containing secret values, or arbitrary proof stdout. Keep detailed diagnostics inside the node/receipt under the user's retention choices.

### 12.5 Error taxonomy and response requirements

| Error code | Meaning | User guidance / state |
|---|---|---|
| `validation_error` | Malformed input, unsupported schema, oversized source | Highlight input and prevent execution |
| `consent_required` | Plugin disabled or check-time execution approval missing | Open appropriate settings/disclosure |
| `environment_missing` | Pinned image/toolchain/import unavailable | Offer separate explicit provisioning; no network action while checking |
| `isolation_unavailable` | Container daemon/policy cannot establish required boundary | Do not run; show setup requirement |
| `source_compile_error` | Syntax/type/elaboration error | Show location-aware Lean diagnostics; not a false-theorem verdict |
| `target_mismatch` | Target theorem missing, renamed, changed or lacks expected type | Show formal statement and challenge mismatch |
| `incomplete_proof` | Incomplete goal, placeholder proof or `sorryAx` | Show as not verified, even if compilation emitted a file |
| `unapproved_axiom` | An axiom or native trust extension violates policy | Show exact offending axiom names and trust explanation |
| `audit_indeterminate` | Auditor incomplete/unsupported or known-bug gate failed | Fail closed; do not imply no axioms |
| `independent_check_failed` | `lean4checker` or comparator replay failed | Name failing stage, preserve diagnostics |
| `resource_limit` | Time/memory/process/file/log limit reached | Show limit; no verified claim |
| `cancelled` | Explicit cancellation or node/session invalidation | No new receipt/current result |
| `stale_result` | Proof/graph/grants/policy changed while running | Retain prior result only as historical; prompt recheck |
| `internal_error` | Tool crash, malformed machine output, cleanup failure | Do not verify; report bounded support data |

Never expose an internal full traceback as an HTML fragment. Return errors through Graphlink's established notification/intent mechanisms with actionable messages. A deliberately forged “Verified” string printed by Lean must have zero effect on status.

## 13. Comprehensive verification test strategy

Tests must be **independent of implementation internals where possible**, and negative cases are first-class release gates. Keep a tiny deterministic stub runner for fast host tests, but only **real pinned Lean/compiler/checker/container runs** may satisfy the proof-soundness and isolation gates.

### 13.1 Proof-soundness fixture matrix

| ID | Fixture / mutation | Expected result |
|---|---|---|
| P01 | Simple `Nat` theorem with complete tactic proof | Positive at allowed assurance level |
| P02 | Parameterized theorem with type classes/universe parameters | Correct elaborated target, positive |
| P03 | Same theorem with a syntax error | Compile error, no verified result |
| P04 | Unsolved tactic goal | Incomplete/compile error, no verified result |
| P05 | `sorry` directly in proof | `sorryAx` rejected |
| P06 | `sorryAx` only in an imported dependency | Rejected by transitive audit |
| P07 | User-declared axiom used to prove an arbitrary proposition | Rejected unless explicitly allowed; never strict by default |
| P08 | Built-in `Classical.choice`, `propext`, `Quot.sound` dependencies | Accurately listed; allowed only under documented policy |
| P09 | Native computation axiom introduced indirectly | Rejected under strict default policy; reported accurately |
| P10 | Theorem with correct name but altered statement (`True` instead of challenge) | Challenge mismatch, rejected |
| P11 | Two theorems; unrelated one compiles but target fails | Target failure, no false positive |
| P12 | Candidate changes notation/coercion/type-class instance to alter meaning | Comparator challenge mismatch or lower level explicitly not upgraded |
| P13 | Known Lean #15226 imported `S9`/constructor example | Exact axiom detected, regardless of imported summary |
| P14 | Same #15226 fixture with declaration ordering/name variation | Same correct dependency result |
| P15 | Mutual inductive with axiom in constructor type | Complete axiom set |
| P16 | Large dependency DAG and recursive/cyclic declarations | Terminates; no incomplete sentinel cache or false negatives |
| P17 | Imported trusted module missing, version mismatch | Environment error; no silent fallback |
| P18 | Untrusted `.olean` or malformed serialized proof data | Independent verifier rejects/isolates safely |
| P19 | `debug.skipKernelTC`-style unchecked theorem injection, where reproducible | Rechecker/comparator rejects |
| P20 | Proof generated by an LLM without human-approved challenge | Cannot achieve high-assurance status |
| P21 | Failed external checker despite successful Lean compile | No independent/high-assurance badge |
| P22 | All stages pass but structured result uses wrong `sourceHash` | Host rejects result |
| P23 | All stages pass but toolchain/checker digest differs | Host rejects result |
| P24 | Printed text says “proof succeeded” while checker exit is nonzero | Host rejects result |

The unit fixture comments must state whether the failure is in elaboration, kernel replay, axiom policy or challenge match; those are different tests. Use actual assertions on parsed checker evidence, not string matching on the expected explanation.

### 13.2 Security and isolation E2E matrix

| ID | Scenario | Expected behavior |
|---|---|---|
| S01 | Docker/Podman binary absent | Feature unavailable; no direct host Lean spawn |
| S02 | CLI installed but daemon stopped | Fail closed, actionable error |
| S03 | Image digest mismatch / wrong toolchain | Reject before untrusted source runs |
| S04 | Image not provisioned, network available on host | No implicit pull/download |
| S05 | Lean tactic tries to read host home/Graphlink secrets | Access denied; secret absent in logs/results |
| S06 | Lean tactic attempts internet, DNS and backend loopback requests | Network denied |
| S07 | Candidate tries to overwrite trusted challenge/imports/checkers | Rejected or container filesystem denies |
| S08 | Candidate tries `lake update`, external command, host binary or malicious file path | No ability to change trusted imports/host |
| S09 | High CPU infinite tactic | Terminated by deadline/limit; no orphan |
| S10 | Memory exhaustion / repeated process spawn | Enforced caps and complete cleanup |
| S11 | Cancel during child/grandchild execution | Whole container removed by ID |
| S12 | Backend session closes mid-job | Job cancelled/owned cleanup; no stale commit |
| S13 | Plugin grant revoked during execution | No result committed as current; work stopped |
| S14 | Source edited during execution | Stale result rejected after final `await` |
| S15 | Current graph changes during execution | No cross-graph mutation |
| S16 | Proof node deleted then ID reused/new node created | Old attempt cannot write to new node |
| S17 | Forged stdout/JSON/diagnostic messages | No host status promotion |
| S18 | Symlinks, `..`, Windows path separators, Unicode and mount labels | Input cannot escape scoped volume/path resolver |
| S19 | Concurrent same-attempt retries and double clicks | One job; idempotent replies |
| S20 | Cleanup command fails once | Retry/visible `cleanup_failed` with orphan detection; no false “stopped” |
| S21 | Missing/untrusted receipt asset on restore | Preserved inert state; no “verified” label |
| S22 | Malicious HTML/JS in Lean error text | Escaped, non-executable text |

Execute security E2E on **real Windows and Linux** with the intended engine; a mock OS guard alone is not evidence of isolation. Document platform exceptions in a fail-closed support matrix rather than silently skipping tests and shipping a misleading platform label.

### 13.3 Graphlink integration and persistence tests

1. Plugin discovery: manifest validated, namespace `formal_lean.proof` stable, picker entry correct, grant-required activation, activation errors isolated from sibling plugins.
2. Empty canvas: create proof node parentless, positioned from `spawn_x`/`spawn_y`; selected parent: create as connected branch.
3. State wire: `pluginState.formalLean` is a JSON **string**, survives scene validation, and never exposes non-finite/unsupported values.
4. Undo/redo: source edit, receipt update, target edit, graph deletion/restore and stale flags round-trip without changing immutable receipt hashes.
5. Save/reload: normal current source, prior receipts, disabled plugin passive state, missing plugin, unavailable asset, corrupted legacy state, unsupported schema and imported workspace.
6. Source revisions: edits during a run, undo to a prior revision, identical text with changed imports/policy, linked parent changes, grant changes, changed environment digest.
7. Transport: wrong plugin ID, unknown intent, invalid args, unauthorized grants, WebSocket disconnect/reconnect, duplicate request/response race.
8. Resource cleanup: window closing, session switching, plugin disabling, attempt timeout, cancellation during container startup, backend shutdown.
9. Packaging: plugin manifest, trusted orchestration module, UI bundle and documentation survive build to **wheel**, import outside checkout, disabled feature without Docker does not break core Graphlink.
10. Existing mathematics: unchanged `science_mathematics` operation identifiers, numeric behavior, old receipts, and existing scientific tests.
11. No hidden dependency: base Python `requirements.txt` and installs remain usable without Lean, Docker and network.
12. Accessibility and text safety: keyboard-only editor, screen reader announcements, diagnostic focus, dark/light mode, XSS-in-diagnostics regression.

### 13.4 Property/fuzz and mutation testing

Use Hypothesis for JSON state versioning, canonical hash invariance under semantic identity, intent argument validation, random Unicode source, source-length boundaries, and graph change sequences. Apply parser fuzzing to runner responses and emitted diagnostics, including nested/oversized JSON, null values, duplicate keys where decoding policy cares, unexpected type coercion, NaN/Infinity, malicious Unicode control characters and asset-ref tampering. Build targeted mutation cases that intentionally remove the stale-state check, skip axiom auditing, accept a forged success string or allow a host fallback; each mutation must be caught by a test.

### 13.5 Browser and performance tests

Add `web_ui/e2e/formal-lean.spec.ts` (happy-path editor, real backend, real Lean runner in CI), `formal-lean-safety.spec.ts` (explicit authorization, result downgrade, stale state, cancel, XSS), and focused Vitest tests for `formalLeanState`, `FormalLeanNodeView`, `SceneCanvas` mappings, and scene store. Preserve `src/app/canvas/renderCountGate.test.tsx`, the token contrast tests, generated wire-type guards, path-collision scanner, and all UI-kit ratchets. The feature must not make the 500-node canvas mount heavy editor or proof assets for every node.

## 14. CI, packaging, documentation and operational policy

### 14.1 Existing project gates: never weaken them

At baseline, [`AGENTS.md`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/AGENTS.md) defines PR jobs: Windows/Python 3.12 `python-tests`, Ubuntu/Python 3.12 `build-check`, Ubuntu/Node 24 `frontend-checks`, and Ubuntu `posix-permissions`. The scientific browser workflow is path-filtered. New Lean code must pass those jobs without deleting tests, loosening coverage thresholds, or increasing performance ceilings to mask regressions.

Recommended developer verification from repository root:

```sh
# Set up Python 3.12, install the pinned production lock and dev tools per AGENTS.md.
python -m compileall -q .
python -m ruff check .
python -m pytest -q -n 2 --cov --cov-report=term-missing --ignore=backend/tests/perf
python -m pytest -q -n0 backend/tests/perf
python contracts/codegen.py --check
npm --prefix web_ui ci
npm --prefix web_ui run check
python -m pip_audit -r requirements.txt

# Packaging verification on a clean environment outside the source checkout:
python -m build
python -m twine check dist/*
# Install the resulting wheel into a fresh venv *outside* the checkout,
# then import the plugin, discover its manifest and exercise a disabled-run path.
```

The exact pinned dev tool versions and extra Linux permission test commands are in `AGENTS.md`; **use that file's current versions** during implementation, not whatever `pip` resolves by default. The full root test run must continue to collect `backend/tests`, `contracts/tests`, `tests`, and `mutation_tests`. Preserve the overall 85% coverage threshold and add the new shipped package to coverage scope if required by `tests/test_coverage_scope.py`. No new loose root `.py` module unless `pyproject.toml`'s explicit module inventory is updated.

### 14.2 New formal-Lean required workflow

Add `.github/workflows/formal-lean.yml` with changes to at least these paths triggering a full Lean-specific job: `plugins/formal_lean/**`, `graphlink_plugins/formal_lean/**`, `formal_lean_runner/**`, its backend/Vitest/Playwright tests, `web_ui/src/app/canvas/FormalLean*`, `formalLean*`, `sceneStore.ts`, `SceneCanvas.tsx`, toolchain lock, manifest and workflow changes. Do not depend on `scientific-workbench.yml` automatically discovering this new family.

**Job A — static/API and stub tests (PR-blocking):** Python contract + state + concurrency tests, frontend Vitest, manifest discovery, target kinds, undo, coverage and packaging checks.

**Job B — actual Lean verification (PR-blocking for Lean-affecting changes):** pin and verify the image/Lean/mathlib manifest; run real positive/negative proof fixtures, `lean4checker --fresh`, comparator where required, axiom audit golden tests including #15226, offline container security tests, and a reproducibility run. The workflow must fail if the daemon or required verifier is missing, not report a skipped suite as success.

**Job C — real browser integration (PR-blocking for UI-affecting changes):** build the production frontend, start a real authenticated Graphlink backend in an ephemeral environment, use Chromium Playwright to create/edit/check/cancel/export and reopen a proof node, and attach traces/screenshots for failures. No retries that convert a flaky proof engine into a green gate.

**Job D — scheduled cross-platform security/perf (report + release-blocking review):** run security isolation and cleanup on Windows plus Ubuntu and a realistic cold/warm mathlib workload; store sanitized measurements. Before a supported Windows release, Windows real-container execution is mandatory even if its heavier job is scheduled on CI.

Consider a dedicated immutable toolchain artifact provisioning workflow with restricted write credentials. Do not download untrusted binary caches in a PR with secrets. Pin third-party CI actions by commit SHA, consistent with existing workflows; document provenance and SBOM/vulnerability scanning of toolchain images.

### 14.3 Generated contract and schema discipline

If the existing `pluginState` string field is sufficient, **do not** add a new Python-to-TS scene contract just to carry formal data. Parse `formalLean` with a dedicated runtime validator in the frontend and test the versioned JSON structure. If eventually changing `contracts/graphlink_scene_payload.py`, regenerate the canonical JSON Schema and TypeScript with `python contracts/codegen.py --write`; commit regenerated artifacts; run generated tests and the frontend checks. Never hand-edit `src/lib/bridge-core/generated/*` or bypass type errors using `as unknown as`.

### 14.4 Packaging and platform setup

Graphlink's `setup.py` bundles plugin manifests and the frontend in wheels. Test that `plugins/formal_lean/plugin.py` and `plugin.toml` are included both in source installs and built wheel output, and that `graphlink_plugins.formal_lean` imports outside checkout. **Do not bundle a gigantic mutable mathlib checkout into the Python wheel.** Distribute the fixed toolchain/image separately and visibly; its installation, cache location, offline posture, disk footprint and removal procedure belong in user documentation.

### 14.5 Documentation requirements

Create and keep in sync:

- `docs/FORMAL_LEAN_USER_GUIDE.md`: install/prerequisites, choosing a trusted statement, editing a proof, interpreting diagnostics, what a receipt proves, rechecking after edits, grants, export/import, uninstall.
- `docs/FORMAL_LEAN_SECURITY.md`: actual sandbox boundaries, host fallback prohibition, limits, attack assumptions, external checker trust model, axiom policy, AI data disclosure, file retention, Windows/Linux parity, remaining risks.
- `docs/FORMAL_LEAN_TOOLCHAIN_LOCK.md`: exact release/toolchain/mathlib/image/checker hashes, supported OS/architectures, reproducible provisioning steps, official upstream links, upgrade path.
- `docs/FORMAL_LEAN_DEVELOPER.md`: plugin SDK contracts, state schema migrations, runner protocol, race guards, test commands, release checklist, known proof-audit issue reproducer.
- README and plugin picker descriptions: say **Lean 4 proof checking**, never “proves anything automatically.” Retain legacy Mathematics and Scientific Workbench descriptions unchanged.


## 15. Milestone plan, dependencies and concrete deliverables

This is an ordered engineering plan, **not** a calendar commitment. A later milestone cannot waive an unmet earlier gate. Make each milestone a focused pull request rather than attempting one monolithic feature merge. All changes remain on review branches until the specified evidence is attached.

| Phase | Implementation work | Required artifact and exit condition | Depends on |
|---|---|---|---|
| P0 — toolchain truth spike | Pin a stable Lean 4/mathlib pair and verify actual command-line interfaces for compiler, independent checker, comparator and axiom traversal. Reproduce upstream issue #15226 in the selected toolchain or document why not applicable. | `FORMAL_LEAN_TOOLCHAIN_LOCK.md`; real positive/negative command transcripts; exact image digest; validated policy for incomplete axiom audits | None |
| P1 — proof contract | Build immutable challenge/proof snapshot schema, diagnostic schema, receipt versioning, result state machine and strict parsing/size limits. | Unit/property tests for hashes, target identity, partial responses, stale states and migrators | P0 interface facts |
| P2 — sealed runner | Implement offline container invocation, protected trusted modules, read-only mounts, bounded logs/resources, kill/cleanup, clean verifier replay and separate audit stage. | Real adversarial runner E2E on Linux; no host fallback; repeatable positive/negative results | P0, P1 |
| P3 — Graphlink backend | Register `formal_lean.proof`, typed plugin intents, grant checks, job manager, command-wrapped edits/commits, conservative state restore and asset receipts. | Backend plugin/undo/persistence/race tests, wheel install outside checkout | P1, P2 |
| P4 — UI | Add focused editor/diagnostics/goal display and a compact node card; explicit “Check” and “Cancel”; receipt/status detail and clear stale results. | Vitest accessibility + keyboard checks; production Playwright happy/negative cases | P3 |
| P5 — security/assurance | Run real hostile Lean fixtures, audit drift and broken environment tests, Windows containment, cross-platform check, failure cleanup and proof-status downgrade tests. | Reviewed threat-model evidence, signed-off severity-zero blocker list | P2–P4 |
| P6 — release readiness | Docs, README, onboarding, supported-platform matrix, install/recovery, targeted CI workflow and baseline regression tests. | PR with complete checklist and CI links; maintainer approval; no skipped mandatory gates | P0–P5 |
| P7 — optional later | Mathlib search, goal state via language-server integration, curated theorem templates, graph-runner read-only checking, AI proof suggestions. | Separate design/consent and security PRs; none required for v1 | P6 |

### 15.1 Required P0 decision log

Before any feature implementation, formally record the following decisions in the new toolchain lock document:

1. **Exact versions:** stable Lean release/commit, mathlib revision, Lake version, dependency manifest and container image digest; a compatibility matrix per supported CPU/OS.
2. **Actual verifier invocation:** verified CLI flags, whether `lean4checker` and `comparator` need additional build outputs or modules, and whether their reported success constitutes the required independent proof check.
3. **Trust assumptions:** allowed base axioms (for example, any use of classical logic is a deliberate policy decision), prohibited `sorryAx`, prohibited user-introduced axioms, permitted imported library definitions, and treatment of unsafe extensions.
4. **Correct axiom traversal:** independently validated algorithm or conservative alternative that does not rely solely on a potentially under-reporting `collectAxioms` path. Include a failing fixture before the fix and a passing fixture after.
5. **Runner boundary:** supported Docker/Podman combinations and validated restrictions on runtime configuration, networking, privilege, mounts, process limits and cleanup. Neither an unverified container installation nor a plugin subprocess alone is acceptable.
6. **User statement contract:** theorem name selection, immutable statement owner, fully elaborated type identity and how the reviewer compares the Lean proposition to the intended informal claim.
7. **Receipt scope:** exactly which artifacts are re-verifiable after a restore/import and which require toolchain availability to recheck. Distinguish an authenticated proof run from a standalone portable certificate.
8. **Failure posture:** whether a specific assurance level is disabled until a reviewed checker/auditor becomes available; never downgrade a requested high assurance check to plain compilation without visibly declining the requested level.

### 15.2 Proposed PR ordering and review ownership

- **PR 1: toolchain/assurance ADR**, no application behavior, approved by someone competent to review Lean's trust model.
- **PR 2: runner protocol/fixture suite**, reviewed for host isolation and completeness of the checker invocation.
- **PR 3: source/receipt/state models**, reviewed for serialization, schema versioning, consent and optimistic concurrency.
- **PR 4: plugin registration and backend integration**, reviewed for undo discipline, bundling, grants and result publication.
- **PR 5: frontend editor and test coverage**, reviewed for typed contracts, accessibility, stale statuses and performance.
- **PR 6: CI/security gate and docs**, reviewed against threat-model matrix and release checklist. If security tests uncover a flaw, fix the implementation in an earlier component before closing the release PR.

The same engineer may own multiple PRs, but the proof-assurance and execution-isolation checks should have independent review. This plan does **not** assert that such reviewers or CI capacity have already been allocated.

## 16. Formal correctness invariants and review checklist

These are normative properties of the proposed system, intended to be translated to executable tests. A feature that violates any property below is incomplete even if its happy-path UI works.

**I-01 — Statement identity.** For every `verified` receipt, the reviewer-approved canonical Lean challenge hash and fully qualified/elaborated target identity must match the exact immutable inputs given to the verifier. A candidate may not alter, shadow, replace or silently generalize that target.

**I-02 — Proof authority.** No parser, model output, notebook display string, subprocess log, compiler warning suppression, ordinary numeric computation or test mock may be the authority for `verified`. Only a successful, bounded verifier result satisfying the explicit level can authorize the status.

**I-03 — Assumptions.** No receipt has a trusted/green label if the axiom analysis is incomplete, failed, unknown or reports assumptions outside the approved policy. Built-in axioms are never assumed “absent”; the policy must explicitly state what is permitted.

**I-04 — Source freshness.** A result is current only if its frozen source, challenge, policy, imported library digest, target, graph dependencies and environment exactly match current node state. Text equality alone is insufficient when imports or upstream links differ.

**I-05 — No stale mutation.** An in-flight check can never commit to a replaced node, changed graph, revised source, revoked plugin grant, cancelled ticket or mismatched session, even if a matching node ID appears after the await boundary.

**I-06 — No hostile execution on host.** On every supported OS, user-controlled Lean source is executed only within the reviewed isolated environment; any container-engine failure, missing image or unsupported platform produces an explicit failure without spawning a host Lean process.

**I-07 — No unapproved file or network access.** The proof container receives no host source tree, home directory, secrets, provider tokens, SSH keys, Graphlink DB or mutable mathlib checkout. Runtime networking is disabled and resource/mount constraints are verified, not merely configured.

**I-08 — Complete lifecycle.** Starting, checking, cancelling, timing out, disconnecting, switching workspaces and shutting down must settle every ticket and reap its process tree. Cleanup failure is visibly recorded and cannot be misrepresented as successful termination.

**I-09 — Consistent persistence.** Save/load/undo/export/import do not transform a stale, damaged, unverified or past proof into a current verified proof. Missing plugin code or detached proof assets preserve recoverable inert source and receipt metadata without executing plugin code.

**I-10 — Traceability.** Every accepted receipt contains schema version, toolchain/image identity, selected assurance level, statement identity, source hash, policy hash, check outcome, trusted checker/auditor stages, time and integrity hashes. A hash proves byte identity, not that a claim is mathematically true.

**I-11 — API robustness.** Malformed inputs and responses never crash Graphlink, bypass grants, leak information through diagnostics, create unlimited jobs or silently truncate material needed for verification.

**I-12 — Compatibility.** Existing `science_mathematics` nodes, scientific workflow IDs, chart receipts, existing canvas kinds, root CI ratchets and released workspace formats keep their behavior unless an explicitly reviewed migration proves otherwise.

**I-13 — No hidden AI authority.** AI assistance is disabled for checking in v1. In any later AI mode, the submitted code is treated as adversarial and the user can inspect both the exact formal claim and the checker result.

**I-14 — Invalid label fallback.** If a new/unknown receipt schema, unsupported checker profile or incomplete evidence is encountered on load, the UI uses `unsupported`, `historical` or `needs recheck`, never `verified`.

**I-15 — Accessible reporting.** All result statuses are conveyed in text in addition to color; diagnostic locations and proof state can be navigated with a keyboard, and raw diagnostics are rendered as inert text.

## 17. Delivery acceptance matrix: release blockers

| Gate | Required demonstration | Blocks v1 when |
|---|---|---|
| G01 Proof positive | Real pinned Lean proves simple Nat theorem and the exact elaborated target is recorded | No real verifier transcript |
| G02 False-target attack | Candidate proves `True` instead of user-approved claim; result must be rejected | Name-only or text-only match accepted |
| G03 `sorry` | `by sorry`, `sorryAx` in imported dependencies and indirect uses must not pass strict policy | Any strict proof passes with unapproved sorry |
| G04 New axiom | `axiom false_proof : False` used to inhabit a target must not pass strict policy | Arbitrary assumptions accepted without explicit review |
| G05 Transitive axioms | Reproducer for Lean issue #15226 and imported-inductive indirect dependency audited correctly, or strict level disabled | Unresolved potential false negative reported as clean |
| G06 Trusted challenge | Proof cannot mutate challenge or shadow mathlib/trusted modules, including through filesystem paths | Writable/import-shadowed challenge |
| G07 Independent replay | Separate validated checker/comparator replay of hostile candidate | High-assurance label without independent validation |
| G08 Runtime isolation | Network disabled, host filesystem unavailable, non-root, privilege/capability restrictions, resource caps and runtime identity verified | Any untrusted check executes on host or can access host secrets |
| G09 Failure cleanup | Slow/infinite proof, memory bomb, child spawn, cancellation, app exit and timeout leave no survivors | Orphaned child/container or misleading success |
| G10 Stale result | Mutate source, imports, graph edges, policy, plugin grant or workspace during check: old result never commits | Race results in current green status |
| G11 Persistence | Save/reopen/import/export, missing assets and disabled plugin preserve safe status/source correctly | Lost proof or false verified restoration |
| G12 Frontend | Keyboard editor, diagnostic messages, dark/light themes and escaped malicious diagnostics pass real browser tests | Unusable editor or executable diagnostics |
| G13 CI/regression | Existing Graphlink `AGENTS.md` gates, new Lean-specific live jobs and wheel checks succeed | Skipped or weakened required checks |
| G14 Toolchain lock | Exact tagged/revisioned Lean/mathlib inputs, image digest and offline cache identities committed | Mutable/moving dependency used in verification |
| G15 Docs | Installation, threat model, assumptions, limits, verification scope, recheck and removal clearly documented | User could reasonably mistake numerical/LLM results for formal proof |
| G16 Reproducibility | Same frozen request and pinned toolchain yield equivalent outcomes and stable identity fields across clean reruns; explain nondeterministic timestamps | Run not independently attributable or reproducible |

Each gate needs an **observable test or command log**, a source-controlled assertion where automatable, and an owner/reviewer. No percentage-complete metric substitutes for a failed release blocker. Passing a CI gate is not a mathematical guarantee against all possible kernel or implementation bugs.

## 18. Risk register, fallbacks and open technical questions

| ID / severity | Risk | Mitigation and explicit fallback |
|---|---|---|
| R01 — Critical | Candidate tactics/macros execute arbitrary code | Reviewed container isolation, no host fallback, resource/security E2E; disable checking on unsupported hosts |
| R02 — Critical | Axiom audit under-reports imported declarations | Independently audited traversal with known reproducer; disable `audited`/higher labels if no trustworthy implementation |
| R03 — Critical | Source or checked theorem differs from user-approved claim | Immutable challenge/solution separation and exact elaborated target binding; reject mismatch |
| R04 — Critical | A stale verification is shown as current after edits | Frozen request identity plus post-await grant/graph/source/policy check and explicit stale status |
| R05 — High | Verifier output forged by malicious source | Trusted wrapper/protocol, independent verification stage, strict stage evidence; never trust stdout claims |
| R06 — High | mathlib/toolchain/caches drift or are poisoned | Reviewed pin and artifact digest, offline verification and immutable image; fail unavailable |
| R07 — High | Windows engine/mount semantics differ from Linux | Real Windows test matrix; disable Windows support until verified |
| R08 — High | Host plugin code mutates graph without undo/event gate | Use `record_command`, existing intent choke point, source-controlled AST ratchets, stale guards |
| R09 — High | Local data leaks through implicit network/package installation | Offline runtime, no auto-download, explicit trusted provisioning and redacted logs |
| R10 — Medium | Long mathlib cold start or heavyweight per-node UI | Prebuilt imports, lazy focused editor, bounded queues, measured budgets, no JS editor per inactive node |
| R11 — Medium | Imported workspaces contain old/corrupt receipt versions | Versioned schema migrator, hard limits, inert fallback, never auto-promote trust |
| R12 — Medium | Users read a Lean theorem as proof of different English wording | Side-by-side approved statement/goal and assumptions, no auto-equivalence claim |
| R13 — Medium | Plugin worker RPC cannot support parentless/state needs safely | Keep trusted orchestration in-process; sandbox only untrusted Lean; no speculative worker-API changes |
| R14 — Medium | Toolchain absent on offline machine | Disabled check with setup guidance; prior receipts remain historical and inspectable |

**Open questions requiring a documented maintainer decision:**

1. Do users need a separate theorem-definition editor, or is a single versioned challenge fixed at creation sufficient for v1? Either can work if the check binds the exact approved challenge.
2. Which assumptions are acceptable in “strict” mode? Distinguish ordinary Lean mathematical foundations from unsound introduced axioms and the precise effect of classical choice.
3. Can the selected Lean release's `comparator` independently enforce the intended target and audit policy, or is a separately compiled helper necessary? Do not ship a high-assurance claim before verifying this empirically.
4. Should historical receipts be portable as human-verifiable JSON+Lean sources or as a separate signed bundle? For v1, preserve hashes and public artifacts, but do not advertise a standalone signature/certificate without its verification implementation.
5. Which platforms/architectures have maintainable, reproducible mathlib images and actual independent checker coverage? Disable rather than pretending unsupported variants pass.
6. Who reviews Lean-side audit helper changes, image rebuilds and policy loosening? A reviewer independent of the feature's primary implementer is preferred.
7. What is the maximum acceptable cold-check latency and storage footprint on supported desktop systems? Measure before setting hard user-facing promises.
8. How should future Builder/Graph Runner access be consented? Maintain `expose_to_builder=False` and no unattended execution until a separate approval/security design is accepted.

## 19. Final implementation handoff

### 19.1 First executable task

Create a branch `agent/formal-lean-toolchain-spike` and implement **only P0**, initially in test-only files and documentation. Pin a Lean/mathlib release, provision the audited isolation image, demonstrate a true Lean proof success, rejection of sorry and a forged/altered target, run the upstream axiom-audit reproducer, then report the exact checker/comparator invocations from a real installed environment. If the independent audit or sandbox cannot be shown correct, stop and document the blocker; do not proceed to a green verification UI.

### 19.2 Minimum PR evidence template

```markdown
## Scope
- Phase: P0/P1/P2/P3/P4/P5/P6
- Targeted invariants: I-__
- Changed paths:

## Verification artifacts
- Pinned Lean/mathlib/image/checker digests:
- Positive Lean fixture command and actual result:
- Negative attack fixture commands and actual results:
- Axiom-audit reproducer result:
- Container security evidence (OS/engine/policy):
- Graphlink Python/JS tests run and results:
- Wheel install-outside-checkout result:
- Browser screenshots/traces (UI PRs only):

## Gaps and release gate disposition
- Tests not run, with reason:
- Failed blockers (must be fixed before release):
- Residual risk / reviewed exception:
```

### 19.3 Definition of done

**Planning is complete when this document is reviewable in the Graphlink repository.** **Implementation is complete only when** G01–G16 are met with tested evidence, reviewers sign off on the release threat model, a real isolated Lean run checks the precise user-approved target, and no incomplete/error/stale state can display as verified. A standalone Markdown plan, even this detailed, is not a functional or proven plugin.

### Sources and audit trail

Graphlink references are pinned to the baseline commit `9cc59d88583da59bef3f2cf00b33389ec8f5daaf`, not floating `main`. Central source files:

- [Graphlink `AGENTS.md`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/AGENTS.md) — contributor rules and required CI.
- [`backend/plugin_sdk.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/backend/plugin_sdk.py) and [`backend/plugins.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/backend/plugins.py) — manifests, host registration, grant/intent dispatch and worker limitations.
- [`plugins/science_mathematics/plugin.toml`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/plugins/science_mathematics/plugin.toml) and [`graphlink_plugins/scientific/packs/mathematics.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/graphlink_plugins/scientific/packs/mathematics.py) — existing numerical Mathematics pack.
- [`backend/domain/graph.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/backend/domain/graph.py), [`backend/session_save.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/backend/session_save.py), [`backend/session_load.py`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/backend/session_load.py) — node state, graph lifecycle and safe persistence fallbacks.
- [`web_ui/src/app/canvas/SceneCanvas.tsx`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/web_ui/src/app/canvas/SceneCanvas.tsx) and [`web_ui/src/app/canvas/scientificScene.ts`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/web_ui/src/app/canvas/scientificScene.ts) — current render/type mapping.
- [`docs/WORKSPACE_ISOLATION.md`](https://github.com/dovvnloading/Graphlink/blob/9cc59d88583da59bef3f2cf00b33389ec8f5daaf/docs/WORKSPACE_ISOLATION.md) — existing isolated agent profile; not itself proof that Lean is confined.
- [Lean reference manual: Validating a Lean Proof](https://lean-lang.org/doc/reference/latest/ValidatingProofs/) — proof checking, challenge/solution, comparator conceptual source; interfaces must be tested on the selected pinned release.
- [Lean issue #15226](https://github.com/leanprover/lean4/issues/15226) — `collectAxioms`/`#print axioms` under-reporting reproducer.
- [mathlib4](https://github.com/leanprover-community/mathlib4) — Lean mathlib project, toolchain/cache ecosystem; pin commit rather than using moving `master`.

**Document-control note:** No source modifications, proof checker execution, test run, CI pass or sandbox audit are claimed by the act of writing this plan. This is a design artifact prepared from source review; every implementation gate above still requires evidence.
