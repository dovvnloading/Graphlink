# Plugin System Refactor Plan

**Project:** Graphite App
**Date:** 2026-07-07
**Status:** Phase 0 complete, Phase 1 started. Fixed so far: Workflow allowlist drift for Code Review Agent (§1.4/§4.7), Gitlink's write-approval state machine + path-safety tests (§2.2/§4.4), Code Sandbox's human-approval gate (§2.1/§4.3), and the additive `PluginSpec`/`PLUGIN_REGISTRY` table (§3.1/§4.8) with drift-detecting tests. Still open: Code Sandbox OS-level hardening (deferred pending a scope decision), and Phase 2-5 (migrating existing hardcoded metadata/isinstance chains onto the registry, one plugin at a time — see §5).

## Scope & Method

"Plugins" in this codebase are the 9 node types registered in [graphite_plugin_portal.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_portal.py): Artifact/Drafter, Code Review Agent, Execution Sandbox (Code Sandbox), Gitlink, Branch Lens (Graph Diff), Quality Gate, Workflow Architect, plus the meta-infrastructure that hosts them (Plugin Portal, Plugin Flyout/Picker, the shared context menu). Their real implementations live in `graphite_app/graphite_plugins/*.py` (400–2300 lines each, ~10,000 lines total).

This review read every plugin implementation in full, plus every integration point that wires a plugin into the rest of the app (`graphite_scene.py`, `graphite_window.py`, `graphite_window_actions.py`, `graphite_session/{serializers,deserializers,scene_index}.py`, `graphite_command_palette.py`). Findings were independently gathered per plugin and cross-checked against the source; specific line numbers below were re-verified directly, not taken on faith from a single pass.

## Executive Summary

The complaint that the plugins are "extremely weak and brittle" is accurate, but the root cause isn't any one plugin's code quality — it's that **there is no plugin system**. `PluginPortal` documents itself as "a centralized manager for discovering, listing, and executing available plugins," but discovery is 13 hardcoded method calls, listing is a hardcoded category table, and execution is a linear string match. Every plugin's node type is registered by hand in at least **7 separate files**, using near-identical boilerplate, with no shared base class, no interface, and nothing that fails loudly if a spot is missed. One instance of that drift was live in the app until this review (§1.4/§4.7) — now fixed.

On top of the systemic problem, three plugins carried their own serious issues: **Code Sandbox** executes arbitrary code with no OS-level sandboxing and installs unpinned/unverified pip packages (§2.1 — now gated behind an explicit human-approval checkpoint; real OS-level hardening is still open pending a scope decision); **Gitlink**'s "only writes after explicit approval" guarantee was enforced by a single dialog box, not a verifiable state (§2.2 — now fixed with a fingerprinted state machine); and **Code Review** (2273 lines) and **Gitlink** (1884 lines) each hand-roll their own GitHub REST client, while **Code Review** and **Quality Gate** each hand-roll their own LLM-JSON scoring-agent skeleton — four different files independently reimplementing two things (still open, tracked in §4.2/§4.4/§4.6).

This document lays out the systemic root cause, two priority fixes that shouldn't wait for a full refactor, a proposed target architecture, a refactor plan for each of the 9 plugins individually, and a suggested phased sequence for doing the work without a big-bang rewrite.

---

## 1. Root Cause: There Is No Plugin System

### 1.1 `PluginPortal` is a lookup table, not a registry

[graphite_plugin_portal.py:70-173](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_portal.py#L70) (`_discover_plugins`) calls `_register_plugin(name=..., description=..., callback=..., category=..., icon=...)` thirteen times, by hand. Each `callback` is a hand-written `_create_X_node` method (e.g. `_create_quality_gate_node`, [graphite_plugin_portal.py:391-417](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_portal.py#L391)) that repeats the same ~15-line shape for every plugin: resolve the parent node → construct `XNode(...)` → append to `parent_node.children` → connect 1-3 Qt signals to hardcoded `main_window.execute_X_node` methods → position the node → `scene.addItem(node)` → append to a hardcoded per-type list (`scene.quality_gate_nodes`) → construct `XConnectionItem(parent, node)` → append to another hardcoded list (`scene.quality_gate_connections`).

`execute_plugin` ([graphite_plugin_portal.py:205-210](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_portal.py#L205)) is a linear scan for a matching `name` string; on miss it `print()`s a warning and returns `None` — no exception, no typed failure, callers can silently no-op (confirmed in practice, see §1.4/§4.7).

### 1.2 The seven-file tax

Adding, renaming, or removing a plugin type requires synchronized hand-edits across at least these files, each with its own copy of the same knowledge:

| File | What it duplicates per plugin type |
|---|---|
| [graphite_plugin_portal.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_portal.py) | registration entry + `_create_X_node` factory method |
| [graphite_scene.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_scene.py) | `self.X_nodes = []` / `self.X_connections = []` lists ([lines 58-94](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_scene.py#L58)), plus the identical `self.artifact_nodes + self.workflow_nodes + self.graph_diff_nodes + self.quality_gate_nodes + self.code_review_nodes + self.gitlink_nodes` concatenation **repeated verbatim 6 times** (lines 243, 309, 335, 520, 718, 941) and per-type removal branches (e.g. line 1374, 1418) |
| [graphite_window.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_window.py) | `isinstance()` checks + a hardcoded display-name string per node type |
| [graphite_window_actions.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_window_actions.py) | `execute_X_node` method + `XWorkerThread` construction + cleanup/error handlers |
| [graphite_session/serializers.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/serializers.py) | per-type serialize branch |
| [graphite_session/deserializers.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/deserializers.py) | per-type deserialize branch |
| [graphite_session/scene_index.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_session/scene_index.py) | per-type index entry |

A grep of any given plugin class name against these files returns near-identical counts across all 7 plugin types (7 hits in `graphite_scene.py`, 2-3 in each session file, 3-9 in window/window_actions) — strong evidence this is copy-paste-driven development, not organic per-plugin variation.

### 1.3 The same "which node type is this" knowledge is hand-copied 3-4 times over

- `graphite_plugin_portal.py`'s registration calls (name strings, [lines 71-173](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_portal.py#L71))
- `graphite_plugin_quality_gate.py`'s `_node_label` class-name → display-name map, all 13 entries hardcoded ([graphite_plugin_quality_gate.py:124-140](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_quality_gate.py#L124))
- `graphite_plugin_graph_diff.py`'s own equivalent node-label map
- `graphite_plugin_workflow.py`'s `WORKFLOW_PLUGIN_ICONS` / `WORKFLOW_ALLOWED_PLUGINS` ([graphite_plugin_workflow.py:30-43](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_workflow.py#L30)) plus a third copy embedded in that plugin's LLM system prompt text

None of these derive from the portal's registration table. They're independent, hand-maintained lists that must agree by pure string equality.

### 1.4 Confirmed live bug caused by this drift — ✅ Fixed for Code Review Agent

`WORKFLOW_ALLOWED_PLUGINS` ([graphite_plugin_workflow.py:30-43](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_workflow.py#L30)) was checked directly: it listed 9 plugin names and omitted both "Branch Lens" and "Code Review Agent" — both of which are live, registered plugins in `graphite_plugin_portal.py` ([lines 104, 120](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_portal.py#L104)). Because `_normalize_plan` silently drops any LLM-recommended plugin name not in that allowlist, the Workflow Architect could never recommend or seed either node, with no error, log, or user-visible indication that anything was filtered.

On closer inspection the two omissions are **not symmetric**:
- **Code Review Agent** fits the standard single-parent-plus-starter-prompt seeding contract exactly like every other seedable plugin (it's already handled in `_seed_plugin_prompt`'s isinstance chain, [graphite_window_actions.py:1675-1677](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_window_actions.py#L1675)). Its omission was a pure drift bug. **Fixed**: added to `WORKFLOW_PLUGIN_ICONS`, the system-prompt's allowed-plugins list and usage rule, and the `_fallback_plan` keyword heuristics in `graphite_plugin_workflow.py`.
- **Branch Lens** genuinely cannot be seeded the same way: `PluginPortal._create_graph_diff_node` ([graphite_plugin_portal.py:483-513](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_portal.py#L483)) ignores `main_window.current_node` entirely and instead requires exactly two matching node types already selected in the scene (`len(selected_nodes) != 2`). Adding it to the workflow allowlist as-is would not fix anything — it would replace a silent no-op with a confusing "Select exactly two branch-tip nodes" warning when a user clicks "Add Node" after a recommendation they can't act on the same way as the others. **Left excluded intentionally.** A real fix requires teaching the seeding flow about plugins with a "requires N pre-selected nodes" contract instead of the single-parent one — tracked as new work in §4.7's refactor plan rather than a one-line allowlist fix.

This is still exactly the failure mode the missing registry predicts (§1.1): a hand-maintained list drifted out of sync with reality, and nothing caught it until this review.

### 1.5 Half-finished package migration (dual import paths)

Every plugin has a root-level compatibility shim, e.g. [graphite_plugin_artifact.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugin_artifact.py) (9 lines, `from graphite_plugins.graphite_plugin_artifact import (...)`), pointing at the real implementation in `graphite_plugins/`. The core app files (`graphite_scene.py`, `graphite_window.py`, `graphite_window_actions.py`, all three `graphite_session/*.py` files) import via the **old root shim path**, while `graphite_plugin_portal.py` itself imports directly via the **new package path**. Both resolve to the same classes today, so nothing is broken, but it means the "real" package boundary was only half-adopted, and any future package reorganization has two call sites to update instead of one.

### 1.6 Duplicated engines across plugins

- **GitHub REST client**: `graphite_plugin_code_review.py`'s `_get_github_token`/`_github_headers`/`_github_request` ([~line 1661](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_code_review.py#L1661)) is near-identical to `graphite_plugin_gitlink.py`'s equivalent ([~line 880](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_gitlink.py#L880)) — independent pagination, error-branching, and base64-decoding logic that will drift apart over time (a rate-limit fix in one won't reach the other).
- **LLM-JSON scoring-agent skeleton**: `CodeReviewAnalyzer` ([graphite_plugin_code_review.py:556-1069](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_code_review.py#L556)) and `QualityGateAnalyzer` ([graphite_plugin_quality_gate.py:243-663](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_quality_gate.py#L243)) share the same shape (system prompt → JSON-fence-stripping regex → `_normalize_*` → heuristic fallback → markdown builder), copy-pasted rather than factored into a shared base.

### 1.7 QThread-per-feature: cooperative cancellation that can't actually cancel

Every plugin's `XWorkerThread.stop()` (Artifact, Code Review, Gitlink, Graph Diff, Quality Gate, Workflow — 6 of 7 worker threads checked) only flips an `_is_running` flag. None of them can interrupt an in-flight blocking call (`api_provider.chat`, `requests.get`) already in progress inside `run()` — `stop()` just suppresses the result once the blocking call eventually returns. This is a systemic, codebase-wide convention (also present outside plugins, e.g. `graphite_agents_core.py`), not a defect unique to any one plugin, but every plugin inherits it. Code Sandbox is the one exception with genuine subprocess-level cancellation, because it was already hardened in a prior bug sweep ([BUG_SWEEP_REPORT.md](C:/Users/Admin/source/repos/graphite_app/doc/BUG_SWEEP_REPORT.md) item 3).

### 1.8 "Agents" vs. "plugins" boundary is inconsistent

Code Sandbox's actual execution engine (`VirtualEnvSandbox`, `CodeSandboxExecutionWorker`, LLM generation/repair agents) lives in root-level `graphite_agents_code_sandbox.py`, not in `graphite_plugins/`, even though Code Sandbox is registered and treated as a plugin everywhere else. Every other plugin is self-contained inside its `graphite_plugins/graphite_plugin_X.py` file.

---

## 2. Priority Fixes (independent of the broader refactor)

These two items are security/data-safety relevant and cheap to isolate — recommend doing them before or alongside the architectural work below, not after it.

### 2.1 Code Sandbox: unconfined execution + unverified installs — ✅ Approval gate added (OS-level hardening still open)

[graphite_agents_code_sandbox.py:294-307](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_agents_code_sandbox.py#L294) runs AI/user-generated Python via `subprocess.Popen` with the full privileges of the host OS user — a venv isolates the Python package graph, not the OS (no container, no seccomp/Job Object, no filesystem/network restriction). [Lines 254-292](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_agents_code_sandbox.py#L254) run `pip install -r requirements.txt` with no `--require-hashes`, no version pinning enforcement, and no package allowlist — any package name a user or the generation LLM writes is fetched from PyPI and its build hooks executed, unverified. Timeouts exist (good) but there's no memory/CPU/disk quota.

**Fixed (approval gate):** `CodeSandboxExecutionWorker.run()` ([graphite_agents_code_sandbox.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_agents_code_sandbox.py)) now pauses on a `threading.Event` right after code is ready (hand-written or freshly generated) and before it touches the venv, pip, or the subprocess — it emits `approval_requested(code, requirements_manifest)` and blocks until the main thread calls `approve()` or `deny()`. `graphite_window_actions.py`'s `_handle_code_sandbox_approval_request` shows a modal confirmation naming the declared packages and the full code (via `setDetailedText`) before allowing the run to continue; declining (or stopping mid-wait) guarantees zero prepare/install/execute calls. Covered by [tests/test_code_sandbox_approval_gate.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/tests/test_code_sandbox_approval_gate.py).

**Still open:** the gate only adds a human checkpoint — it does not change what happens once approved. Real OS-level hardening (containerization, Job Objects, hash-pinned installs, resource quotas) is still recommended as a follow-up and was deliberately deferred pending a decision on adding new runtime dependencies (e.g. Docker) versus a stdlib-only approach.

### 2.2 Gitlink: the write-approval gate is UI-only, not structural — ✅ Fixed

[graphite_plugin_gitlink.py:1611-1656](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_gitlink.py#L1611) gated every disk write behind a single `QMessageBox.question` call — there was no separate "approved" flag or fingerprint on `pending_changes`; "approved" meant "whichever dict happened to be in `self.pending_changes` when the button was clicked." Path-traversal defenses (`_normalize_repo_path`, `_safe_local_target`, [lines 146-163](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_gitlink.py#L146)) were actually solid, but had **zero test coverage** despite being the entire security boundary for local writes.

**Fixed:** approval is now an explicit `DRAFT → PREVIEWED → APPROVED → APPLIED` state machine (`GITLINK_STATE_*` constants) with a `_fingerprint_changes()` hash stamped at confirmation time and re-verified immediately before any file is written — if the pending change set was mutated between the dialog and the write, the apply is refused instead of silently proceeding. `_normalize_repo_path`/`_safe_local_target` now have dedicated tests in [tests/test_gitlink_path_safety.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/tests/test_gitlink_path_safety.py), including Windows drive-letter and UNC-path injection cases.

---

## 3. Proposed Target Architecture

1. **A real `PluginSpec` registry.** Replace `PluginPortal._register_plugin`'s loose dict with a `PluginSpec` dataclass: `key, display_name, description, category, icon, node_cls, connection_cls, worker_cls, seedable: bool`. `PluginPortal` becomes a thin lookup over `PLUGIN_REGISTRY: dict[str, PluginSpec]` built once. `_node_label`, `WORKFLOW_PLUGIN_ICONS`/`WORKFLOW_ALLOWED_PLUGINS`, and graph_diff's node-label map all become `PLUGIN_REGISTRY[key].display_name` lookups instead of hand-copied dicts.
2. **A generic node-creation path.** One `PluginPortal.create_node(key, parent_node)` that uses `PluginSpec` fields to do the resolve/construct/position/addItem/list-append/connect dance generically, replacing the 13 near-identical `_create_X_node` methods. Per-plugin special cases (e.g. Graph Diff's two-source-node contract) become an explicit, small `SpecialSelection` hook rather than a bespoke method.
3. **Generic scene bookkeeping.** Replace the ~15 hardcoded `self.X_nodes`/`self.X_connections` list pairs and their 6x-repeated concatenation in `graphite_scene.py` with `self.plugin_nodes: dict[str, list]` / `self.plugin_connections: dict[str, list]` keyed by plugin key, and a single `all_plugin_nodes()` helper instead of the repeated concatenation chain.
4. **A `seed_prompt(text)` protocol method** on every plugin node, replacing the hardcoded isinstance chain in `_seed_plugin_prompt` ([graphite_window_actions.py:1655-1685](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_window_actions.py#L1655)) that currently must know each node type's private widget attribute name.
5. **Shared `graphite_plugins/common/` subpackage:**
   - `github_client.py` — a `GitHubRepoClient` used by both Code Review and Gitlink instead of two hand-rolled copies.
   - `llm_json_agent.py` — a base class for the JSON-fence-stripping / normalize / fallback-heuristic pattern shared by Code Review and Quality Gate.
   - `popup_combo.py` — Code Review's custom themed combo-box widget, generically reusable.
6. **Consolidate Code Sandbox** into `graphite_plugins/` (e.g. a `code_sandbox/` sub-package: `engine.py`, `agents.py`, `ui.py`) so the agents/plugins boundary confusion (§1.8) goes away.
7. **A single per-node worker-thread map** on `main_window` (e.g. `self.plugin_workers: dict[node_id, QThread]`) replacing the ad hoc single shared attribute (`self.sandbox_thread`, `self.quality_gate_thread`, `self.workflow_thread`, etc.) that currently gets silently clobbered when two nodes of the same plugin type run concurrently.

None of this requires a big-bang rewrite — see §5 for a phased path that lets old and new plugins coexist during migration.

---

## 4. Per-Plugin Refactor Plans

### 4.1 Artifact / Drafter

**File:** [graphite_plugins/graphite_plugin_artifact.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_artifact.py) (604 lines)

**Overview:** Split-pane node for iteratively drafting a Markdown "living document" via chat instructions. Standard triad: `ArtifactConnectionItem`, `ArtifactNode` (bulk of the file), `ArtifactWorkerThread` wrapping a small `ArtifactAgent`.

**Weaknesses:**
1. Dead vestigial code: `graphite_plugin_portal.py:352-354`'s `if not hasattr(scene, 'artifact_nodes'): ...` is unreachable — `graphite_scene.py:68/89` already unconditionally initialize both lists. The "will be formalized later" comment is stale; it already was.
2. `main_window.artifact_thread` is a single shared attribute ([graphite_window_actions.py:1311](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_window_actions.py#L1311)), not per-node — a second concurrent Artifact node overwrites the first's thread reference with nothing tracking or cancelling the original.
3. The "stop" icon shown by `set_running_state(True)` ([~line 471-478](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_artifact.py#L471)) has no click handler wired to actually stop anything — purely cosmetic, implies a cancel capability that doesn't exist.
4. `ArtifactAgent.get_response`'s tag-parsing regex ([~line 104-113](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_artifact.py#L104)) silently treats the entire raw LLM response as the new document body on any parse mismatch, fabricating a generic "I have updated the document" message — can silently corrupt the artifact.
5. Chat/preview rendering feeds arbitrary AI/user text through `markdown.markdown()` straight into `QTextEdit.setHtml` with no escaping ([~line 444](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_artifact.py#L444)); the resulting HTML is persisted and replayed verbatim on reload.
6. Deserializer reaches directly into `node.instruction_input`/`node.chat_display`/`node.chat_html_cache` rather than through an accessor — no model/view boundary.
7. Not exposed via the command palette (no reference found), unlike other discoverability paths.

**Refactor Plan:** Extract `ArtifactAgent` and tag-parsing into a standalone testable module with an explicit typed parse result (no silent full-text fallback); add unit tests for parsing edge cases. Track threads per-node (`main_window.artifact_threads: dict[node, thread]`) and wire the stop icon to a real cancel. Separate `ArtifactNode` into a thin Qt view plus a plain `ArtifactDocumentState` model so serializers stop poking widget internals directly. Sanitize/escape literal HTML before markdown rendering.

**Effort/Risk:** **M.** Thread-safety fix touches shared `graphite_window_actions.py` patterns used by sibling plugins. Model/view split is riskier because serializers depend on current attribute names — must preserve the on-disk schema while changing only the in-memory access path.

---

### 4.2 Code Review Agent

**File:** [graphite_plugins/graphite_plugin_code_review.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_code_review.py) (2273 lines — largest plugin, >2x the average)

**Overview:** Reviews a local or GitHub source file with deterministic scoring, structured findings, and a weighted 8-category report (correctness, reliability, security, maintainability, readability, testing, performance, architecture) plus a verdict. Its size comes from three things bundled into one file: a ~290-line custom themed combo-box widget, a ~230-line hand-rolled GitHub REST client duplicated with Gitlink, and a full scoring/rubric engine duplicated in shape with Quality Gate.

**Weaknesses:**
1. GitHub client (`_get_github_token`/`_github_headers`/`_github_request`, [~1661-1691](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_code_review.py#L1661)) is near-duplicated with `graphite_plugin_gitlink.py:880-911`.
2. `CodeReviewAnalyzer` ([556-1069](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_code_review.py#L556)) shares its exact skeleton with `QualityGateAnalyzer` — copy-pasted, not factored.
3. `CodeReviewComboPopup`/`CodeReviewPopupComboBox` ([42-330](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_code_review.py#L42)) reimplement Qt combo-popup positioning purely for theming — ~290 lines with no counterpart elsewhere to share it.
4. Broad exception handling throughout (`get_response` [1056-1068](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_code_review.py#L1056), `load_local_file` [1756-1786](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_code_review.py#L1756), the GitHub load methods [1802-1921](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_code_review.py#L1802)) — network errors, JSON errors, and auth failures are all flattened into the same generic status string with no logging.
5. `CodeReviewWorkerThread.stop()` ([1094-1096](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_code_review.py#L1094)) can't interrupt an in-flight GitHub/LLM call already in `run()` — same systemic pattern as §1.7, worse here because these calls are the slowest in the app.
6. Scoring weights/thresholds are hardcoded as module globals and then duplicated a second and third time in prose inside markdown-builder strings ([356-384](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_code_review.py#L356), [393-441](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_code_review.py#L393), [824-826](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_code_review.py#L824)) — three places to keep in sync by hand.
7. `_fallback_review` ([853-1004](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_code_review.py#L853)) is a second, independent regex-heuristic scoring mechanism that silently substitutes for the LLM rubric on any exception, using different magic numbers than the primary rubric — the two can disagree on methodology while presenting an identical-looking report.
8. God-class: `CodeReviewNode` spans ~1140 lines mixing UI construction, GitHub networking, local file I/O, and badge rendering with no separation.
9. No tests anywhere for the scoring/normalization logic despite it being pure and cheap to test.

**Refactor Plan:** Extract the scoring engine into `graphite_plugins/code_review/scoring.py` (zero Qt imports, directly testable). Build the shared `common/github_client.py` (§3.5) and have both Code Review and Gitlink depend on it. Build the shared `common/llm_json_agent.py` base and have `CodeReviewAnalyzer`/`QualityGateAnalyzer` subclass it. Move the custom combo popup to `common/popup_combo.py`. Split `CodeReviewNode` into a thin view plus a `CodeReviewSourceController` (GitHub/local load) and a `CodeReviewReviewPresenter` (badge/report rendering). Centralize the scoring weights/thresholds in one place and generate the markdown descriptions from it instead of hand-duplicating.

**Effort/Risk:** **L** for the full split (largest, touches networking + a custom widget + scoring + view logic at once). An **M-sized, low-risk first slice**: extract just the GitHub client and scoring engine into pure-Python modules — both are self-contained and don't touch the widget layer. Biggest risk: `graphite_window_actions.py`, serializers, and deserializers all import `CodeReviewNode`/`CodeReviewConnectionItem` by name and consume its output dict keys directly (`review_markdown`, `quality_score`) — any refactor must preserve those shapes or update all dependents in lockstep.

---

### 4.3 Execution Sandbox (Code Sandbox)

**Files:** [graphite_plugins/graphite_plugin_code_sandbox.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_code_sandbox.py) (860 lines, UI/view) + [graphite_agents_code_sandbox.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_agents_code_sandbox.py) (470 lines, root-level execution engine)

**Overview:** Runs a task description or hand-written Python inside a per-node, on-disk virtualenv with declared `requirements.txt` dependencies. Execution engine (`VirtualEnvSandbox`, `CodeSandboxExecutionWorker`, generation/repair LLM agents) lives entirely outside `graphite_plugins/`, inconsistent with every sibling plugin's self-contained layout.

**Weaknesses:** See §2.1 for the priority security findings (unconfined subprocess execution, unpinned/unverified pip installs). Beyond that:
1. `main_window.sandbox_thread` ([graphite_window_actions.py:1024](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_window_actions.py#L1024)) is a single shared attribute, not per-node — concurrent sandbox nodes can clobber each other's thread reference (partially mitigated by a per-node `worker_thread` check, but the shared attribute is dead/misleading state).
2. No `worker_thread.wait()` before `deleteLater()` in cleanup ([graphite_window_actions.py:1057](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_window_actions.py#L1057)) or in `CodeSandboxNode.dispose()` — risk of a "destroyed while still running" condition under timing pressure.
3. `_is_error_output`'s naive keyword matching (e.g. `"exception:"` substring match) can misclassify a script that legitimately prints the word "Exception:" as a failure, triggering a wasted repair cycle.
4. Business logic living outside `graphite_plugins/` (§1.8) — UI file imports `SandboxStage` directly from the agents module with no interface boundary.
5. No tests for `sync_requirements`'s hash-caching logic or the attempt/repair loop.

**Refactor Plan:** Consolidate into `graphite_plugins/code_sandbox/` (`engine.py`, `agents.py`, `ui.py`). Introduce an `SandboxExecutionPolicy` (timeout, memory cap, package allowlist, network mode) as a prerequisite for real hardening. Remove the shared `main_window.sandbox_thread` singleton in favor of the per-node dict from §3.7. Add `wait()` calls before `deleteLater()`. Add tests for requirements-hash caching and error-output classification.

**Effort/Risk:** Consolidation + coupling cleanup: **M**. Thread-lifecycle fixes: **S**. Real sandbox hardening (containerization/Job Objects/pip verification): **L**, and should be scoped as its own security workstream per §2.1 rather than folded into this refactor's timeline.

---

### 4.4 Gitlink

**File:** [graphite_plugins/graphite_plugin_gitlink.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_gitlink.py) (1884 lines — second largest plugin)

**Overview:** Fetches a GitHub repo (REST API or local checkout) into a stitched XML context bundle, sends it plus a task prompt to an LLM, parses a JSON change-set, renders a diff preview, and writes files to disk only after a confirmation dialog. Combines three external-facing concerns (GitHub API, local filesystem, LLM call) in one god-class.

**Weaknesses:** See §2.2 for the priority write-gate finding. Beyond that:
1. GitHub token is stored in plaintext at `~/.graphlink/session.dat` ([graphite_licensing.py:426-427,451-453](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_licensing.py#L426)) — consistent with how other API keys are handled in this codebase (systemic, not unique to Gitlink), but worth fixing alongside the write-gate work since both touch credential/trust boundaries.
2. `zipfile.extractall` on a downloaded GitHub zipball ([~1150-1179](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_gitlink.py#L1150)) has no signature/hash verification before extraction — low risk since the source is GitHub's own API, but worth noting.
3. Every GitHub call funnels through a bare `except Exception as exc: self.set_status(f"Error: {exc}")` pattern repeated ~6 times — no distinction between transient network errors and real failures.
4. `GitlinkWorkerThread.stop()` can't interrupt an in-flight blocking `api_provider.chat` call — same systemic pattern as §1.7.
5. Path-safety helpers (`_normalize_repo_path`, `_safe_local_target`) are the entire security boundary for local writes and have **zero test coverage**.

**Refactor Plan:** Extract `gitlink_github_client.py` (shared with Code Review per §3.5). Extract `gitlink_context_builder.py` (XML context assembly, pure functions, unit-testable independent of the node). Formalize the write gate as `gitlink_write_gate.py`: an explicit `DRAFT → PREVIEWED → APPROVED → APPLIED` state machine that fingerprints the exact file list shown to the user and refuses to apply unless the fingerprint still matches at write time.

**Effort/Risk:** **L** for the full modularization, **S** for the standalone write-gate + path-safety-test fix (recommended to do first, independent of everything else — see §2.2). Main breakage risk: session (de)serialization and portal instantiation both reach into `GitlinkNode` internals (`repo_state`, `context_xml`, `pending_changes`) directly, so any class-boundary change must preserve those attribute names/shapes.

---

### 4.5 Branch Lens (Graph Diff)

**File:** [graphite_plugins/graphite_plugin_graph_diff.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_graph_diff.py) (897 lines)

**Overview:** Compares two independently-selected branch-tip nodes and renders a side-by-side transcript plus an LLM-generated divergence summary. Structurally unusual: takes `left_source_node, right_source_node` instead of a single parent, and `self.parent_node` is unconditionally `None`. "Branch diff" is actually two independent linear walks up each side's `parent_node` chain (`_collect_branch_nodes`), not a real graph/tree comparison — there's no shared-ancestor detection, so a common prefix between the two branches is duplicated into both transcripts and sent to the LLM as if unrelated.

**Weaknesses:**
1. No shared-ancestor detection in the branch walk (see above) — semantically wrong for branches that share history, and wastes prompt budget.
2. Cycle guard (`while cursor and id(cursor) not in seen`) silently truncates on a cycle rather than logging/surfacing it — masks a scene-integrity bug elsewhere instead of failing loudly.
3. `_extract_node_text` is a ~90-line `hasattr`/`getattr` grab-bag enumerating ~15 unrelated per-plugin attribute names — every new sibling plugin field requires a manual update here, and it has no test coverage.
4. `valid_sources` tuple is hardcoded independently in `graphite_plugin_portal.py:487` and this file's node-label map, and the two lists don't fully agree (portal's tuple and this file's label map were built independently).
5. The two-node selection contract (`len(selected_nodes) != 2` at [graphite_plugin_portal.py:490](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_portal.py#L490)) gives no indication of which two nodes, and ordering is incidental to selection order rather than explicit.
6. Deserialization silently drops the whole node (and its connections) from the loaded scene if either source-node index fails to resolve — no warning, silent data loss on session load.
7. `GraphDiffAnalyzer.get_response` catches bare `Exception` and falls back silently with no logging — network failure and malformed-JSON failure look identical to the user.
8. `create_graph_diff_note`/`execute_graph_diff_node` in `graphite_window_actions.py` are near-verbatim copies of the Quality Gate equivalents, differing only in field names — a shared helper is conspicuously absent.

**Refactor Plan:** Extract `_collect_branch_nodes`, `build_branch_payload`, `_extract_node_text`, and `GraphDiffAnalyzer` into a Qt-free `graphite_plugins/graph_diff_core.py` for unit testing. Add real shared-ancestor detection to the branch walk. Introduce a shared `get_branchable_node_types()` used by both the portal and this file instead of two independently maintained lists. Log (and surface via notification banner) any source-index resolution failure during deserialization instead of silently dropping the node. Factor `execute_graph_diff_node`/`create_graph_diff_note` and their Quality Gate twins into one shared `run_llm_worker(...)` helper in `graphite_window_actions.py`.

**Effort/Risk:** **M.** Core extraction is mechanical and low-risk (the functions are already pure), but touches 6+ call sites. Biggest risk: changing `valid_sources` semantics and inadvertently allowing/disallowing a node type other plugins rely on for branch-tip selection.

---

### 4.6 Quality Gate

**File:** [graphite_plugins/graphite_plugin_quality_gate.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_quality_gate.py) (1512 lines — third largest)

**Overview:** Walks the branch lineage back to the root, sends the flattened transcript to an LLM with a "production readiness" rubric, and renders a verdict, a 0-100 score, and up to 4 recommended follow-up plugins with seeded starter prompts (via `plugin_requested` → `instantiate_seeded_plugin`). Structurally the closest sibling to Code Review — both are "review/scoring" plugins with a near-identical analyzer shape.

**Weaknesses:**
1. `_node_label` ([124-140](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_quality_gate.py#L124)) hardcodes the same 13 class-name → display-name pairs that the portal's registration already owns; a near-identical map exists a third time in `graphite_plugin_graph_diff.py`.
2. A second, independent allowlist/icon map (`QUALITY_GATE_PLUGIN_ICONS`/`QUALITY_GATE_ALLOWED_PLUGINS`) keyed by *display name* rather than class name — a third keying scheme for the same 9-13 plugins, all hand-maintained in this one file.
3. `QualityGateAnalyzer` duplicates `CodeReviewAnalyzer`'s exact skeleton (§1.6/§4.2) — same JSON-fence-stripping regex, same fallback-heuristic-scoring pattern, no shared base.
4. `_fallback_review`'s keyword-regex scoring adds/subtracts fixed point values (e.g. +12 for "has_code", -18 for "has_errors") based on substring matches over the whole transcript — trivially gamed by the literal word "test" appearing in prose; not real analysis.
5. `main_window.quality_gate_thread` is a single shared attribute, not per-node — same concurrency clobbering risk as Code Sandbox and Artifact.
6. `_setup_ui` is a single ~286-line method building the entire multi-tab widget tree, inline stylesheets, and badge logic.
7. Bare `except Exception: pass` in `_read_widget_text` and in thread-signal disconnects during `dispose()` hides real errors.
8. No tests anywhere for the scoring heuristics, payload-building, or JSON normalization, despite all of it being pure logic.

**Refactor Plan:** Build the shared `common/llm_json_agent.py` base (§3.5) and have `QualityGateAnalyzer` and `CodeReviewAnalyzer` both subclass it instead of duplicating ~150 lines each. Extract the rubric/fallback-scoring logic into a Qt-free `graphite_quality_rubric.py` exposing a plain `score_branch(transcript, goal, criteria) -> ReviewResult`. Replace `_node_label` and the display-name allowlist with lookups against the registry from §3.1. Split `_setup_ui` into smaller builder methods. Track threads per-node instead of the single shared attribute.

**Effort/Risk:** **M** (touches `graphite_plugin_portal.py`, `graphite_plugin_code_review.py`, `graphite_plugin_graph_diff.py`, and `graphite_window_actions.py` — 4-6 files). Risk is moderate: the JSON-normalization output must stay byte-for-byte compatible with persisted session fields (`readiness_score`, `verdict`, `recommendations` are reconstructed directly from saved data in `graphite_session/{serializers,deserializers}.py`), so schema drift during extraction could break old save-file loading.

---

### 4.7 Workflow Architect

**File:** [graphite_plugins/graphite_plugin_workflow.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_workflow.py) (1087 lines)

**Overview:** Takes a free-text goal/constraints, asks an LLM for a JSON execution blueprint, and renders recommended-plugin cards with "Add Node" buttons that seed new plugin nodes end-to-end via `plugin_requested` → `main_window.instantiate_seeded_plugin` → `PluginPortal.execute_plugin` (string match) → `_seed_plugin_prompt` (isinstance chain poking a starter prompt into whichever widget field that node type exposes). This is the most "meta" plugin in the codebase — it must know about every other plugin type to recommend/seed them, which is exactly where the missing registry hurts most (see §1.4's confirmed live bug).

**Weaknesses:**
1. Triple-duplicated plugin enumeration in one file: `WORKFLOW_PLUGIN_ICONS`, `WORKFLOW_ALLOWED_PLUGINS` (derived from the same dict, at least not a fourth independent list), and a third copy embedded in the LLM system prompt text — none derived from the portal's registry.
2. **Confirmed drift, partially fixed** (§1.4): "Code Review Agent" was missing from the allowlist/icons/system-prompt/fallback-heuristics and has been added. "Branch Lens" remains intentionally excluded since it can't be seeded via the single-parent-plus-prompt contract this file uses (see §1.4) — a real fix needs a "requires pre-selected nodes" seeding path, not an allowlist edit.
3. `_normalize_plan` silently drops any LLM-recommended plugin not in the allowlist with a bare `continue` — no logging, no user-visible signal, falls through to a fully-generic fallback plan with no indication anything was filtered.
4. `PluginPortal.execute_plugin`'s failure mode is `print()` + `None` — `instantiate_seeded_plugin` then silently no-ops; clicking "Add Node" visibly does nothing with no error.
5. `_seed_plugin_prompt` ([graphite_window_actions.py:1655-1685](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_window_actions.py#L1655)) is a fourth hardcoded isinstance chain that must independently know each node type's private input-widget attribute name; any plugin missing from the chain silently drops the seed prompt.
6. `WorkflowArchitectAgent.get_response` mixes prompt construction, network call, JSON parsing/repair, normalization, and markdown rendering in one method with a bare `except Exception` that masks the real failure reason.
7. No cancel affordance wired to `WorkflowWorkerThread.stop()` — nothing in `execute_workflow_node` ever calls it.
8. No tests for `_fallback_plan`'s keyword heuristics or `_normalize_plan`'s filtering.

**Refactor Plan:** Once the registry (§3.1) exists, derive `WORKFLOW_ALLOWED_PLUGINS`/icons/system-prompt text from `PluginPortal.get_plugins()` metadata instead of three hand-copied lists — this fixes §1.4's live bug as a side effect. Extract `WorkflowArchitectAgent` into a Qt-free module for unit testing. Make `_normalize_plan` return dropped-plugin names so the UI can surface "N recommendations were dropped" instead of failing silently. Make `PluginPortal.execute_plugin` raise a typed error (or return a result type) instead of `print`+`None`, and have `instantiate_seeded_plugin` show a notification banner on failure. Replace the `_seed_plugin_prompt` isinstance chain with a `seed_prompt(text)` protocol method implemented per plugin node (§3.4).

**Effort/Risk:** **M** for the full plan; **S** if only fixing the immediate drift bug (adding the two missing names to the three lists) plus adding failure logging. The `seed_prompt` protocol migration is the riskiest single piece — it touches every plugin node class and must preserve existing side effects (e.g. some nodes also call an `_on_X_changed()` callback after setting text) exactly, or a seeded prompt silently stops working for one node type.

---

### 4.8 Plugin Portal (meta-infrastructure)

**File:** [graphite_plugins/graphite_plugin_portal.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_portal.py) (614 lines)

**Overview:** This is the file that should be the plugin system, and isn't (see §1.1). It also directly imports every other plugin module ([lines 3-19](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_portal.py#L3)) and contains 13 hand-written factory methods.

**Weaknesses:** All of §1.1-§1.4 live here. Additionally: `_resolve_branch_parent` special-cases `CodeNode` ([line 229](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_portal.py#L229)) with no documented reason why that one node type needs different parent resolution — an undocumented special case future plugin authors won't know to replicate or avoid. `PLUGIN_CATEGORY_META` ([22-48](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_portal.py#L22)) is a separate hardcoded list that must stay in sync with the `category=` string used in each `_register_plugin` call, with a silent "More Plugins" catch-all bucket if they ever drift ([194-201](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_portal.py#L194)) — a drift-tolerant fallback that hides the bug instead of surfacing it.

**Refactor Plan:** This is the centerpiece of §3 — implement `PluginSpec`/`PLUGIN_REGISTRY` and the generic `create_node(key, parent_node)` path here. Once done, this file should shrink from ~614 lines of hand-written factories to a short declarative table plus one generic method with named hooks for the handful of genuinely special cases (Graph Diff's two-source selection, System Prompt's note-based implementation).

**Effort/Risk:** **L**, but additive — the registry and generic path can be built and tested alongside the existing hardcoded methods, then each `_create_X_node` migrated one at a time (see §5), so this doesn't require a single risky cutover.

**✅ Phase 1 done:** `PluginSpec` (dataclass) and a module-level `PLUGIN_REGISTRY: dict[str, PluginSpec]` now exist in this file, populated for all 13 plugins with `key`/`display_name`/`description`/`category`/`icon`/`node_cls`/`connection_cls`/`seedable`. `get_plugin_spec(key)` and `get_display_name_for_node(node_or_cls)` are available as the intended eventual replacement for the hand-copied name maps in `graphite_plugin_quality_gate.py`/`graphite_plugin_graph_diff.py`/`graphite_plugin_workflow.py` — **not yet wired into them**, per the phased plan (that's Phase 2+, one plugin at a time). `_discover_plugins` and all 13 `_create_X_node` factories are untouched. [tests/test_plugin_registry.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/tests/test_plugin_registry.py) cross-checks the registry against the live `PluginPortal` registration (name drift) and against `graphite_window_actions.py`'s `_seed_plugin_prompt` isinstance chain (`seedable` flag correctness) — the same class of drift that caused the Workflow allowlist bug in §1.4, now caught by a test instead of a future audit. As a side effect, `ArtifactNode`/`ArtifactConnectionItem` moved from a deferred import inside `_create_artifact_node` to a top-level import (no circular-import reason existed for the deferral, confirmed before the change).

---

### 4.9 Plugin Flyout / Picker + Context Menu

**Files:** [graphite_plugins/graphite_plugin_picker.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugins/graphite_plugin_picker.py) (410 lines), [graphite_plugin_context_menu.py](C:/Users/Admin/source/repos/graphite_app/graphite_app/graphite_plugin_context_menu.py) (92 lines, root-level — not migrated into `graphite_plugins/` even though every plugin node imports it)

**Overview:** `PluginFlyoutPanel` is the popup UI that lists categories/plugins from `PluginPortal.get_plugin_categories()` and emits `pluginSelected`. `PluginNodeContextMenu` is the shared right-click menu ("Open Document View" / "Collapse" / "Delete") used by every plugin node via `getattr`-based capability detection (`supports_branch_context_toggle`, `toggle_collapse`).

**Weaknesses:** This is the healthiest part of the plugin system — it's a genuinely generic, data-driven UI that iterates `get_plugin_categories()`/`get_plugins()` rather than hardcoding per-plugin widgets, and the context menu's `getattr`-based capability checks are a reasonable lightweight duck-typing pattern (arguably a model for what §3.4's `seed_prompt` protocol should look like elsewhere). The only real issue: `graphite_plugin_context_menu.py` lives at the repo root while everything that uses it lives in `graphite_plugins/`, continuing the same half-finished-migration pattern as §1.5.

**Refactor Plan:** Move `graphite_plugin_context_menu.py` into `graphite_plugins/graphite_plugin_context_menu.py` (or `graphite_plugins/common/context_menu.py`) as part of the same migration pass that resolves §1.5's dual import paths. No other changes needed — once the registry (§3.1) exists, this file needs zero modification since it already consumes the portal generically.

**Effort/Risk:** **S.** Pure file-move plus import-path updates; low risk since behavior doesn't change.

---

## 5. Suggested Migration Sequencing

Doing all of this at once would be its own brittle, high-risk rewrite. Recommended phases, each independently shippable:

1. **Phase 0 (do first, independent of everything else):** the two priority fixes in §2 — Code Sandbox execution hardening and Gitlink's write-gate state machine + path-safety tests. The Workflow allowlist drift (§1.4/§4.7) is already fixed for Code Review Agent as part of this review; Branch Lens's exclusion needs the seeding-flow enhancement described in §4.7, not a quick list edit.
2. **Phase 1 (additive, no existing behavior changes):** build `PluginSpec`/`PLUGIN_REGISTRY` and the generic `create_node()` path in `graphite_plugin_portal.py` (§3.1-§3.2) alongside the existing hardcoded `_create_X_node` methods — don't delete anything yet.
3. **Phase 2 (prove the pattern on the simplest cases):** migrate the two structurally simplest plugins (Artifact, Graph Diff — no GitHub client, no dual-with-sibling duplication) onto the registry end-to-end, including the `seed_prompt` protocol (§3.4) and per-node scene bookkeeping (§3.3). Use this to validate the pattern before touching the more complex plugins.
4. **Phase 3 (shared engines):** extract `common/github_client.py` and `common/llm_json_agent.py` (§3.5); migrate Code Review, Gitlink, and Quality Gate onto them independently of the registry work.
5. **Phase 4 (finish the registry migration):** migrate the remaining plugins (Code Sandbox, Workflow, Quality Gate, Code Review, Gitlink) onto the registry one at a time; each migration should delete that plugin's entries from the hardcoded isinstance chains in `graphite_scene.py`/`graphite_window.py`/`graphite_window_actions.py`/session files as it goes, rather than leaving both paths live indefinitely.
6. **Phase 5 (cleanup):** once all 9 plugins are on the registry, delete the now-empty hardcoded factory methods in `graphite_plugin_portal.py`, resolve the dual import paths (§1.5), move `graphite_plugin_context_menu.py` into the package (§4.9), and remove any remaining vestigial code identified along the way (e.g. §4.1's dead `hasattr` check).

This ordering front-loads the two genuine security/safety risks, proves the new architecture on low-complexity plugins before betting the complex ones on it, and keeps every intermediate state shippable.
