# Graphlink — Production Readiness Roadmap

**From "impressive concept" to "SOTA production desktop app."**

Status date: 2026-07-11. Owner model: solo maintainer + AI agents (so the plan favors
high-leverage automation and CI-enforced invariants over process/headcount).

This roadmap was produced by auditing the actual codebase across four expert lenses —
distribution/release engineering, quality engineering, security/privacy, and product/UX —
and then re-sequenced by dependency. Each workstream below names concrete files, tool
choices, and a binary "done" criterion. Effort tags: `days` / `week` / `weeks` / `month+`.
Priority tags: `P0` (blocks shipping a trustworthy build), `P1` (core product), `P2`
(differentiator), `P3` (later / demand-gated).

---

## 1. Where Graphlink stands today

**Strong foundations already in place** (much of it hardened over the recent review passes):

- Multi-provider LLM support (Ollama, llama.cpp, OpenAI-compatible, Anthropic, Gemini)
  with per-request state snapshotting (no mid-request provider races).
- SQLite persistence with atomic single-transaction writes and stable UUID node identity.
- DPAPI-encrypted secrets at rest (Windows), with tested legacy migration.
- Approval gates on both code-execution surfaces (Py-Coder, Execution Sandbox).
- 368 headless tests, green in GitHub Actions on `windows-latest`.
- LOD rendering infrastructure wired through every node paint path.

**The gaps between here and "production" cluster into four themes**, each a section below:

| Theme | One-line gap |
|---|---|
| Distribution | Runs only as `python graphlink_app.py` from source. No installer, no signing, no auto-update, no packaged build. |
| Quality | Widgets *are* the data model; worker threads read live scene objects; no crash reporting; no E2E/visual tests. |
| Security & Privacy | "Execution Sandbox" overclaims isolation; web→codegen prompt-injection chain; unpinned supply chain; secrets silently plaintext off-Windows. |
| Product & UX | No undo/redo, no streaming, no crash recovery, no onboarding, no node copy/paste, no shareable graph files. |

---

## 2. The sequencing that matters

Most workstreams are independent, but a handful of ordering constraints dominate. Getting
these wrong causes rework or ships a dangerous build:

1. **Signing must precede packaging/auto-update.** The moment an installer ships, its
   update path must already verify what it installs. Adding auto-download onto today's
   unauthenticated `update_signal.md` would turn a GitHub-account compromise into RCE on
   every user. → *Update-channel integrity* and *code signing* start in week 1, in parallel
   with packaging.
2. **Undo must precede every new mutating feature.** Copy/paste, templates, and import each
   add operations that otherwise have to be retrofitted into undo later (quadratic cost).
3. **The worker `RequestContext` refactor precedes streaming.** Streaming that mutates node
   widgets from worker threads would amplify the exact thread-safety class we're closing.
4. **The secret-storage abstraction precedes any macOS/Linux build.** `protect()` silently
   falls back to plaintext off-Windows; a cross-platform build today would ship GitHub
   tokens in cleartext.
5. **The packaging foundation (installable package + frozen-path support) precedes
   everything in Distribution** — it's the base all freezing/installer/CI work sits on.

**The one deliberate accepted rework:** do the thread-safety `RequestContext` refactor
*before* the full model-layer extraction, even though some snapshot-building is redone
later. The thread-safety crash risk is a live field-crash generator and can't wait a month
for the model layer; the `RequestContext` dataclass survives that migration intact.

---

## 3. Phased plan (what to actually do, in order)

### Phase 0 — Stabilize the core (in progress)
Ship-blocking correctness/safety that doesn't depend on packaging. **Several items here were
completed this session** (see §8). Remaining P0-class engine work:

- **Worker architecture: `RequestContext` + per-node workers + real cancellation**
  (`weeks`, quality). Finishes findings #20/#21/#22. Below.
- **Crash reporting** (`days`, quality). `faulthandler` + `excepthook` + opt-in GitHub
  submission. Below.
- **Crash recovery / dirty-state / autosave trust** (`days`, product). Below.
- **Undo/redo foundation** (`week`, product). Below.
- **Honest execution-isolation labeling** (`days`, security) — Phase 1 of the sandbox
  workstream is a pure-copy fix and should land immediately. Below.

### Phase 1 — Ship a trustworthy Windows build
The distribution spine plus the security invariants that a public binary demands.

- Packaging foundation → PyInstaller freeze → Inno Setup installer + code signing →
  tag-driven release CI → verified auto-update. (Distribution, all P0/P1.)
- Supply-chain hygiene (lockfile, pip-audit, Dependabot, SBOM) — `days`, do early; it makes
  every dependency bump a safe agent chore.
- Update-channel integrity (Ed25519-signed manifest) — must land with/before the installer.
- Prompt-injection defenses for the web→codegen chain.
- Test pyramid (pytest-qt widget + offscreen E2E smoke + visual regression + coverage gate).

### Phase 2 — SOTA product surface
What separates "neat" from "the best tool in the category."

- Streaming token display + response cancellation.
- Node/subgraph copy-paste; `.graphlink` export/import; subgraph templates.
- First-run onboarding (backend wizard, sample graph, guided tour).
- Command-palette maturity; structured logging; performance budgets + harness.
- Model-layer extraction (finding #40) — the highest-leverage architecture refactor,
  incremental via the serializer seam.

### Phase 3 — Platform + reach (demand-gated)
- Cross-platform secret storage (keyring) → macOS notarized `.dmg` + Linux AppImage.
- Accessibility & theme baseline; optional chat-DB encryption; docs site + demo media.

---

## 4. Distribution & release engineering

| Workstream | Pri | Effort | Essence & "done" |
|---|---|---|---|
| **Packaging foundation** | P0 | week | `pyproject.toml`, entry point, split `requirements` (llama-cpp-python → optional extra), `.python-version` pinned to **3.12** (llama-cpp/PyInstaller have no 3.14 wheels), frozen-aware `graphlink_paths.py`, bundled tiktoken cache. **Done:** `uv pip install -e .` + `graphlink` launches from any cwd; suite still green. |
| **Windows freeze (PyInstaller onedir)** | P0 | week | `graphlink.spec` with aggressive Qt excludes (QtWebEngine is used by the HTML Renderer — keep it; drop Qt3D/Charts/Quick/QML/Pdf), `matplotlib.use('qtagg')`, `build_windows.ps1`, a `--smoke-exit` frozen smoke test. **Done:** clean-machine build launches to a working canvas, all 8 node types constructible, <350 MB uncompressed. |
| **Installer + code signing** | P1 | week | Inno Setup per-user install (no UAC), upgrade-in-place preserving `~/.graphlink`; **start the signing-identity application in week 1** (Azure Trusted Signing ≈ $10/mo → SignPath OSS → Certum). Sign inner exe *and* installer. **Done:** signed `GraphlinkSetup-x.y.z.exe`, SmartScreen shows publisher not "Unknown". |
| **Release CI (tag-driven)** | P1 | days | `release.yml` on `v*` tags: verify tag == version, test, build, frozen-smoke, sign, Inno, `SHA256SUMS`, GitHub Release. `CHANGELOG.md` + `scripts/prepare_release.py`. **Done:** `git push --tags` ships a signed release unattended in ~20 min; version-mismatched tag fails loudly. |
| **Auto-update (verified)** | P1 | week | Repoint `graphlink_update.py` at the GitHub Releases API; "Download & install" verifies **SHA256 + Authenticode** before executing; never run anything fetched from a branch. Retire `update_signal.md`. **Done:** one-click verified upgrade with `chats.db` intact; tampered asset refused. |
| **First-run experience** | P2 | week | Ollama probe, provider onboarding, crash-visibility (Help → "Open log folder"). **Done:** fresh machine reaches a working model without reading docs. |
| **macOS/Linux** | P3 | month+ | Keyring abstraction now (cheap); notarized `.dmg` + AppImage later, gated on demand. |

**Top risk:** *llama-cpp-python is the packaging landmine* — no wheels for newer Pythons,
CPU/CUDA variants can't coexist, 50–500 MB swings. Decide in the packaging workstream to
ship it as an **optional runtime-installed extra** (Ollama stays the built-in local path),
or it dictates the build Python forever.

---

## 5. Quality engineering

| Workstream | Pri | Effort | Essence & "done" |
|---|---|---|---|
| **Worker architecture (#20/#21/#22)** | P0 | weeks | `RequestContext` frozen dataclass built entirely on the UI thread; workers receive plain data, never a live `QGraphicsItem`; results routed by `persistent_id` (no use-after-free on a deleted node); finish #21 by giving every launchable node a `worker_thread` + a `WorkerRegistry`; event-driven cancellation into provider request loops. **Guardrail:** an AST test that fails any worker `run()` referencing `.scene()`/`.parent_node`. **Done:** no worker constructor accepts a scene item; AST test in CI; cancel returns to idle <2 s. |
| **Crash reporting** | P0 | days | `graphlink_crash.py` called first: `faulthandler` to `~/.graphlink/crash/`, `sys`/`threading` excepthooks writing a **redacted** JSON report (version/OS/node counts — never chat content), `qInstallMessageHandler`; next-launch "closed unexpectedly" dialog with **opt-in** prefilled GitHub-issue submission. No Sentry by default (documented decision). **Done:** native fault and Python exception each produce a redacted report; redaction test proves no chat content leaks. |
| **Model-layer extraction (#40)** | P1 | month+ | One `@dataclass` state per node type behind the existing serializer seam; strangler migration one node type per PR, each gated by a golden-payload test; then move JSON+base64 encoding off the UI thread into `SaveWorker`. **Done:** `grep toPlainText\|toHtml` in `graphlink_session/` returns nothing; old saves load byte-identically. |
| **Test pyramid** | P1 | weeks | `pytest-qt` widget tests, offscreen E2E smoke with a `FakeProvider` at the `api_provider` seam, Pillow screenshot regression (CI-platform baselines, perceptual threshold), coverage gate 85% on the logic core, `pytest-timeout` on every Qt test. **Done:** E2E send→save→reload runs green per PR in <5 min; a broken signal connection fails the widget tier. |
| **Structured logging** | P2 | week | Convert `print()`/`except: pass` to module loggers (ruff T201 enforces), `run_id` + thread in every line, JSON-lines file format, log-level setting. **Done:** ruff T201 clean; startup phases and worker lifecycles logged. |
| **Performance budgets** | P2 | week | `doc/PERFORMANCE_BUDGETS.md`, a synthetic graph factory, `pytest-benchmark` (serialize/deserialize/search/LOD render sweep), nightly regression job. **Done:** budgets documented; a 2× slowdown trips the nightly threshold; LOD sublinearity holds at 500 nodes. |

**Top risks:** the model-layer migration stalling half-done (mitigate: one node type per PR
+ golden tests + grep-based done-criterion); AI-agent-authored features reintroducing the
single-slot/scene-read pattern (mitigate: the AST architecture test as a hard CI gate).

---

## 6. Security & privacy

| Workstream | Pri | Effort | Essence & "done" |
|---|---|---|---|
| **Honest execution isolation** | P0 | weeks | **Phase 1 (days, do now):** relabel "Execution Sandbox" → "Execution Environment — venv (dependency isolation, NOT a security boundary)"; approval dialog states "runs with your full user permissions." **Phase 2 (week):** ctypes **job object** (`KILL_ON_JOB_CLOSE`, memory/process caps) so children can't orphan or memory-bomb. **Phase 3 (opt-in):** Windows Sandbox `.wsb` "Contained" mode. `doc/SANDBOXING.md` records the decision. **Done:** no UI string claims isolation the code lacks; every sandbox child dies on stop/exit; memory bomb is capped. |
| **Prompt-injection defenses** | P1 | week | Spotlight/delimit web content (`<untrusted_web_content>` + anti-instruction system rule); propagate a taint flag through history into codegen; approval + GitLink dialogs banner web-tainted context; deterministic hidden-text/`ignore previous instructions` screen. **Done:** every prompt embedding web content uses untrusted markers; approval flags tainted context; covered by tests. |
| **Cross-platform secret storage** | P1 | week | `keyring` as preferred backend behind the existing `protect()/unprotect()` API (sentinel prefix like `dpapi:`); backend ladder keyring → DPAPI → **plaintext only with a persistent visible warning**; per-secret badge in settings. **Done:** secrets land in the OS credential store on all three OSes; plaintext never silent. |
| **Supply-chain hygiene** | P1 | days | `pip-compile --generate-hashes` (or `uv lock`), `pip-audit` in CI (PR + weekly cron), Dependabot, CycloneDX SBOM, SHA-pinned Actions. **Done:** hash-pinned reproducible installs; pip-audit gates PRs; SBOM per run. |
| **Update-channel integrity** | P1 | week | Ed25519-signed `releases.json` manifest, pinned public key in `graphlink_update.py`, verify-before-parse, downgrade protection. **Must land before the installer.** **Done:** unauthenticated/downgraded signals refused (tested). |
| **Network-egress transparency** | P2 | week | `graphlink_egress.py` registry (single source of truth) + a CI test failing on any unregistered `http(s)://` host; a "Network & Privacy" settings page with per-destination toggles + an Offline mode. **Done:** every destination shown with a working toggle; Ollama+offline = provably zero egress. |
| **Threat model + SECURITY.md + invariant tests** | P1 | days | `doc/THREAT_MODEL.md` (assets, trust boundaries, in/out-of-scope attackers); enable GitHub Private Vulnerability Reporting; `tests/test_security_invariants.py` (egress grep, approval-before-exec call-order, no-plaintext-when-backend-available). **Done:** invariant tests in CI; a deliberately broken invariant fails the build. |
| **Chat-DB at-rest posture** | P3 | weeks | `doc/DATA_AT_REST.md` (honest: full-disk encryption is the real control); **opt-in** SQLCipher gated on a working secure-secrets backend; clean up `%TEMP%` sandbox dirs on node delete. **Done:** doc published; opt-in encryption never stores its key in plaintext. |

**Top risks:** the sandbox trust-inversion (mislabeled isolation trains rubber-stamp
approvals) and the end-to-end web→codegen injection path — both real *today*, both fixed
cheaply by labeling + spotlighting + approval salience before any deeper isolation work.

---

## 7. Product & UX polish

| Workstream | Pri | Effort | Essence & "done" |
|---|---|---|---|
| **Undo/redo foundation** | P0 | week | Snapshot-based `UndoManager` (bounded deque of `serialize_chat_data()` payloads, zlib-compressed) pushed before each mutating op; Ctrl+Z/Ctrl+Shift+Z; refuse undo across a node with a live worker. Granular `QUndoCommand`s later, after the model layer. **Done:** Ctrl+Z restores a deleted multi-selection with connections; 15+ headless tests. **Sequence first** — everything mutating depends on it. |
| **Crash recovery & dirty-state** | P0 | days | Debounced autosave on `scene_changed` (2.5 s); a `~/.graphlink/running.lock` sentinel → next-launch "didn't shut down cleanly, restored" notice; a truthful Saved/Saving…/Unsaved title-bar indicator; escalate repeated save failures to a persistent Retry + "copy JSON backup" strip. **Done:** `kill -9` after moving nodes → relaunch restores to within ~3 s; title bar always answers "is my work saved?". |
| **Streaming + cancellation** | P1 | weeks | `stream=True` SSE/NDJSON path per provider; worker emits **throttled (~30–50 ms batched)** `chunk_received(node_id, delta)` signals; `ChatNode.append_streaming_text`; Stop button preserving partial output. **Must be built on the `RequestContext` worker refactor.** **Done:** tokens appear <500 ms; canvas stays 60 fps during streaming; Stop preserves partial output. |
| **Node/subgraph copy-paste** | P1 | week | Serialize selected nodes + internal connections to the system clipboard under `application/x-graphlink-nodes`; paste with fresh IDs + offset; Ctrl+C/V/D; cross-chat paste falls out for free. Depends on undo. **Done:** copy a 5-node branch, paste into another chat intact, Ctrl+Z removes it. |
| **`.graphlink` export/import** | P1 | week | Wrap `serialize_chat_data()` as a shareable file; **a single exhaustively-tested secret-scrub pure function** used by export/templates/clipboard; import is inert (approval gate intact); PNG/SVG canvas export. **Done:** exported file contains zero secrets/tokens/local paths; round-trips on a second machine. |
| **First-run onboarding** | P1 | week | `completed_onboarding` flag; backend wizard (probe Ollama / GGUF picker / API key) ending in a verified 1-token test call; a bundled sample `.graphlink` graph (doubles as a CI fixture); 5–6 coach-mark tour; non-blank empty-canvas hint. **Done:** fresh machine → first AI response in <3 min. |
| **Command-palette maturity** | P2 | days | Fuzzy subsequence scorer, recency/frequency, shortcut hints in rows, `>`-prefix node jump, generated Ctrl+/ cheat-sheet. Every new action registers here as its definition-of-done. |
| **Subgraph templates** | P2 | week | "Save selection as template" → `~/.graphlink/templates/*.graphlink`; picker section; 4–6 bundled starters (also CI fixtures). Built on copy-paste + the scrub function. |
| **Accessibility & theme baseline** | P2 | weeks | Finish the inline-hex → `get_semantic_color()` token sweep; high-contrast + light themes with CI contrast checks; visible focus ring distinct from selection; `QAccessible` names + node-focus announcements (NVDA-verified). |
| **Docs site + demo media** | P3 | week | MkDocs Material on GitHub Pages; generated shortcut reference; 3 demo GIFs + a 90 s walkthrough; template gallery. Sequence *after* streaming/onboarding so the media shows the polished experience. |

**Top risks:** undo debt compounding if any mutating feature ships first; streaming
colliding with the threading debt (mitigate: build on `RequestContext`, add GUI-contention
stress tests); and **secret leakage through the new sharing surfaces** — export, templates,
and clipboard must all route through one adversarially-tested scrub function.

---

## 8. Already fixed this session

These landed as part of the review-and-repair pass that produced this roadmap (each with
regression tests; see `doc/ARCHITECTURE_REVIEW_FINDINGS.md`):

- **Critical data-loss race** — an in-flight autosave restored the previous chat's id after
  new-chat/load-chat, so the next autosave overwrote the wrong chat's row. Fixed with a
  context-epoch guard in `ChatSessionManager`.
- **HTML Renderer network-egress sandbox** — the renderer auto-ran AI-generated/session HTML
  with JS enabled and no restrictions (exfiltration + localhost SSRF). Now every request is
  filtered through a local-only URL interceptor on a dedicated WebEngine profile.
- **Finding #20 (partial)** — chat requests now resolve the branch system prompt on the UI
  thread and hand the worker plain data, so `ChatWorker` no longer walks live scene objects.
  (The general `RequestContext` refactor in §5 finishes the class of issue.)

---

## 9. Suggested first two weeks

A concrete near-term slice that unblocks the most while carrying the least risk:

1. **Week 1:** packaging foundation (`pyproject.toml`, deps split, frozen paths) **+** start
   the code-signing application (long lead time) **+** supply-chain lockfile & pip-audit
   **+** the honest sandbox-relabel (`days`) **+** crash reporting (`days`).
2. **Week 2:** PyInstaller freeze + frozen smoke test **+** undo foundation **+** crash
   recovery/dirty-state **+** the Ed25519 update-manifest signing (before any installer).

That sequence produces a signable, freezable build with crash visibility and safe-work
guarantees — the minimum honest "1.0" spine — without having touched the deeper model-layer
or streaming work that Phase 2 can absorb incrementally.
