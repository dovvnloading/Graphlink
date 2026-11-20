# CI contract for contributors and coding agents

This file applies at the repository root and throughout Graphlink. It describes the checks that actually run in [`.github/workflows/ci.yml`](.github/workflows/ci.yml), plus its separately triggered browser and nightly workflows. Treat the workflow, `pyproject.toml`, `web_ui/package.json` and the named tests as the executable source of truth if they change. Do not bypass a failing ratchet by weakening its assertion, increasing its budget, excluding source, or skipping a test: fix the regression, or make and explain a deliberate, reviewed baseline/contract change.

## When and where CI runs

- The `CI` workflow runs on **pull requests targeting `main`** and on manual `workflow_dispatch`; there is **no push-to-`main` run**. A new push to the same PR cancels the previous in-progress run.
- **Every PR must pass four independent jobs:** `python-tests` on **Windows / Python 3.12**; `build-check` on **Ubuntu / Python 3.12**; `frontend-checks` on **Ubuntu / Python 3.12 + Node 24**; and `posix-permissions` on **Ubuntu / Python 3.12**. The Windows job is required for real DPAPI, Windows Job Objects and ACL behavior; a Linux-only pytest run cannot replace it.
- The fifth job, `e2e-playwright`, **does not run on PRs**. It runs only when `CI` is manually dispatched with `run_browser_tests=true`. The independent `nightly-perf.yml` and `nightly-mutation.yml` workflows are scheduled/manual, **not PR-blocking**; see below.
- Run Python commands from the **repository root**: narrowing the working directory or selecting only `backend/tests/` silently leaves other test directories uncollected. Node commands below use `web_ui/` as their working directory. The production requirements lock is resolved for **Python 3.12**, and `web_ui/.nvmrc` pins **Node 24**.

## First-push validation

Set up Python 3.12 and Node 24, then run from the repository root (a Windows machine gives the closest match to the Python job). The exact CI dev-tool versions matter, and these tools deliberately are not in the production lock:

```sh
python -m pip install -r requirements.txt
python -m pip install pytest==9.1.1 pytest-cov==7.1.0 pytest-xdist==3.8.0 ruff==0.16.4 hypothesis==6.165.10 setuptools==82.0.1
npm --prefix web_ui ci
python -m compileall -q .
python -m ruff check .
python -m pytest -q -n 2 --cov --cov-report=term-missing --ignore=backend/tests/perf
python -m pytest -q -n0 backend/tests/perf
npm --prefix web_ui run check
python -m pip install pip-audit==2.10.1
python -m pip_audit -r requirements.txt
```

On **Linux**, additionally run the exact permissions selection used by CI (it deliberately does not collect the whole Windows-dependent backend suite):

```sh
python -m pytest -q -n0 -m posix_permissions backend/tests/test_chat_library.py backend/tests/test_db_backup.py backend/tests/test_knowledge_store.py backend/tests/test_backend_secrets_at_rest.py backend/tests/test_mcp_server.py backend/tests/test_instance_lock.py
```

Run `cd web_ui && node scripts/audit-with-retry.mjs` for the separate npm dependency audit. For package/build work, also run `python -m pip install build==1.5.0 twine==7.0.0`, `python -m build` and `python -m twine check dist/*`. The real `build-check` additionally verifies a clean wheel install **outside the checkout**, described below. If a local OS cannot exercise Windows DPAPI or Linux mode-bit behavior, do not treat its skips as passing those jobs.

## Python job: syntax, lint, security audit and complete test suites

| Gate (job `python-tests`) | What must be true |
| --- | --- |
| Install | `requirements.txt` installs successfully on Python 3.12; CI separately installs the pinned pytest/coverage/xdist/Ruff/Hypothesis/setuptools versions above. Do not assume dev tooling comes from the production lock. |
| `python -m compileall -q .` | **Every** discovered Python file parses/compiles, including tests and tools. Run from the root, not just `backend/`. |
| `python -m ruff check .` | `pyproject.toml` selects **`E9` (syntax), `F` (Pyflakes), `T201` (no `print()`)**, targets `py312` and uses 120-character formatting guidance (not a blanket line-length lint gate). Fix undefined/unused names and prohibited prints; `F401` is excepted only for `backend/canvas.py` and `api_provider.py` compatibility facades. `T201` is excepted only for the six named CLI files in `[tool.ruff.lint.per-file-ignores]`. Prefer structured logging in runtime code. |
| Main pytest + coverage | `python -m pytest -q -n 2 --cov --cov-report=term-missing --ignore=backend/tests/perf` must pass from the **root**. It collects `backend/tests/`, `contracts/tests/`, `tests/` and `mutation_tests/`. Do not replace it with a narrowed test run. `--strict-markers` and `--strict-config` reject unknown markers/config. `DeprecationWarning`, `PendingDeprecationWarning`, `FutureWarning` and Starlette deprecations are errors, apart from the two explicitly documented ignores in `pyproject.toml`. A test hanging for 60 seconds produces faulthandler diagnostics; this is **not** a timeout-based pass or skip. |
| Coverage floor and scope | The overall report must be **at least 85%** (`[tool.coverage.report].fail_under`), over the production packages/modules listed in `[tool.coverage.run].source` plus `contracts`, excluding tests. Adding a shipped module/package requires adding it to coverage source; neither excluding hard-to-test code nor counting test code as production is a valid fix. |
| Performance pytest | `python -m pytest -q -n0 backend/tests/perf` must pass **serially and without coverage**. `test_loop_watchdog.py` measures representative mutations and a 500-node publish: the smaller worst event-loop stall from two runs must be **≤100 ms**. A separate AST ratchet requires **zero direct `render_chart_png` / `render_chart_svg` calls under `backend/`**; move expensive rendering off-loop using `asyncio.to_thread` instead of raising the ceiling. The real 16 ms product goal is not a flaky per-PR timing assertion. |
| Python dependency vulnerability scan | `python -m pip_audit -r requirements.txt` (pip-audit 2.10.1) must succeed. Remedy vulnerabilities in the hash-locked production dependency set; there is no CI ignore list in this step. |

All collected functional tests are gates, not only the named ratchets: backend unit/integration/security/DPAPI tests, desktop-launch checks, scientific/numerical tests, generated contract tests and the four `mutation_tests/test_*_properties.py` property suites must remain green. Add or update focused tests with behavior changes, especially for authentication, approvals, persistence, schema compatibility and security boundaries.

## Python structural, contract and golden ratchets (run by main pytest)

These are **enforced tests**, even when a change looks unrelated. Keep their source-of-truth inventories aligned in the same PR:

| Test(s) | Rule for a passing change |
| --- | --- |
| `tests/test_case_insensitive_paths.py` | No **Git-index-tracked** paths may differ only by case or slash style on a case-insensitive Windows checkout. Rename or remove collisions; the same check is `web_ui`'s `check:paths`. |
| `tests/test_coverage_scope.py` | Coverage `source` must cover **all** `[tool.setuptools.packages.find].include` packages and `[tool.setuptools].py-modules`, include the intentional build-time `contracts` exception, name only existing modules, and contain no unrelated padding. Root modules are entered **without `.py`**. |
| `tests/test_dependency_lock_currency.py` | `requirements.in` direct package names must match the direct-entry annotations in `requirements.txt` **exactly**, and every locked entry must have hashes. Regenerate on **Python 3.12** with `pip-compile --generate-hashes --output-file=requirements.txt requirements.in` when dependencies change. The test does **not** check changed version constraints or transitive freshness: installation/audit still matter. |
| `tests/test_doc_currency.py` | `update_signal.md` must equal `graphlink_version.APP_VERSION`; all entries in `GRAPHLINK_REPO_NAVIGATION.md`'s **Concrete File Index** must resolve. Update the index when moving/deleting referenced files. |
| `tests/test_domain_purity.py` | `backend/domain/` cannot import the forbidden infrastructure list (FastAPI, uvicorn, events, notifications, agents, composer, token_counter, app, API provider, etc.), make bus-shaped `publish`/`dispatch_intent`/`register_topic`/`register_intent` calls, or invoke dynamic-import escape hatches. Keep domain code pure and move side effects to outer layers. |
| `tests/test_mixin_declaration_bases.py` and `tests/test_settings_mixin_contract.py` | `SettingsManagerParts`, `SceneDocumentParts` and `DispatcherParts` remain **type-only**, with no runtime members/`__init__`, and occupy the last MRO position before `object`. Every family `*Ops` mixin inherits its declaration base. Keep the settings declaration's class/member inventory and `_protect_and_track(self, value)` signature in sync with reality. |
| `tests/test_no_production_asserts.py` | No Python `assert` statements in shipped root modules or shipped packages: `python -O` strips them. Raise a real exception for runtime invariants; assertions belong in tests. |
| `tests/test_no_qt_anywhere.py` | Qt removal is **permanent**: `qt_burndown.json` is **0 source, 0 test, 0 total**, no PySide6/PyQt imports anywhere (especially under `backend/`, `web_ui/` or `graphlink_desktop.py`), and no PySide6/PyQt/qtawesome dependency in `pyproject.toml` or `requirements*`. Never raise the pin. |
| `tests/test_node_state_migration.py` | Migrated per-kind node fields are accessed via `node.state.<field>`, not a bare attribute or bare string-based `getattr`/`setattr`. `SceneNode`'s **exact 14 core fields**, the **golden scene wire-key set**, and non-owning-kind wire defaults must stay compatible. When intentionally changing the wire contract, review/update the golden expectations and corresponding frontend/generated consumers, not just the implementation. |
| `tests/test_posix_permission_coverage.py` | Every file containing `@pytest.mark.posix_permissions` must appear in the `posix-permissions` job's explicit six-file collection list, and every listed file must actually contain marked assertions. If adding/moving a marked test, update `.github/workflows/ci.yml` in the same PR. |
| `tests/test_python_support_floor.py` and `tests/test_toolchain_metadata.py` | Keep `requires-python = ">=3.12"` and Ruff `target-version = "py312"`. Native GUI extras must be platform-scoped and Qt-free; `mutmut` is only in the non-Windows `mutation` extra, not the general `dev` extra. |
| `tests/test_register_function_length.py` | Every production `backend/**` `register*` function (sync or async, including nested) is **≤300 lines**. No more than **four** may sit within **30 lines** of that cap; the scanner must still find at least **20**. Split oversized/near-cap registration functions; do not raise either ceiling. |
| `tests/test_shared_node_field_docs.py` | The exact discovered sets of node kinds passing `content` or `history` to `SceneNode(...)` must match the pinned sets and field comments. If a new kind legitimately uses one, update **both** `backend/domain/model.py` comments and the test sets. |
| `tests/test_undo_classification_gate.py` + `tests/undo_classification.py` | Every backend `register_intent(topic, intent, handler)` uses classifiable literal topic/intent strings, has **one** matching A/B entry **with a reason**, and leaves no stale entries or duplicate registrations. Class **A** handlers must reach `record_command`; class **B** handlers must not. Use resolvable same-file handlers, or extend the gate when introducing a new handler shape. |
| `backend/tests/test_packaging_py_modules.py` | `[tool.setuptools].py-modules` must exactly equal all root `*.py` files except `setup.py`. Add newly shipped root modules and remove stale manifest names in the same change. |
| `backend/tests/test_prompt_registry.py` | Every registered live prompt must match its canonical SHA-256, nonempty text and version **≥1**. On a prompt edit, **bump its version and recompute its hash** in `graphlink_prompts.PROMPT_REGISTRY`. Adding/removing a prompt also requires updating the pinned `EXPECTED_PROMPT_IDS` inventory. |
| `backend/tests/test_packaging_bundled_resources.py` | Packaging must replace stale compiled SPA chunks/plugin manifests on rebuild; no removed artifact may survive in a reused wheel build directory. Keep the `setup.py` build hook's replacement behavior correct. |
| `contracts/tests/test_generated_artifacts.py` | Generated JSON Schema/TypeScript files must exist, match `contracts/codegen.py` output byte-for-byte, use JSON Schema 2020-12 and remain Qt-free. The codegen registry must match what the SPA actually imports; unsupported schema constructs must fail loudly, and saved chart formats must remain compatible. Run `python contracts/codegen.py --write` and commit **both** generated TS and schema changes after editing payload dataclasses. |

## Frontend job: audit, types, lint, Vitest and production build

The Ubuntu `frontend-checks` job runs `npm ci` in `web_ui/`, then `node scripts/audit-with-retry.mjs`, then **`npm run check`**. The latter runs, in order:

1. **`npm run check:paths`** — `python ../tools/check_case_collisions.py`, rejecting tracked case-insensitive path collisions. Windows `python-tests` repeats this check and a production build on a real case-insensitive checkout.
2. **`npm run check:schema`** — `python ../contracts/codegen.py --check`, which fails on missing or stale generated TypeScript/JSON Schema. Regenerate with `python contracts/codegen.py --write` from the repo root; commit the outputs.
3. **`npm run typecheck`** — `tsc --noEmit`; types must agree with generated wire contracts. Do not hide missing generated fields behind unsafe casts.
4. **`npm run lint`** — ESLint's recommended JS/TypeScript rules plus React Hooks and JSX accessibility rules (and the React-refresh warning). Fix violations including unused variables; intentionally unused arguments/variables/destructured elements may use a leading underscore per `eslint.config.js`.
5. **`npm run test`** — the complete Vitest/jsdom unit, integration, property and repo-hygiene suite in `src/app/` and `src/lib/`. Playwright's `e2e/` and `e2e-safety/` are excluded from Vitest.
6. **`npm run build`** — Vite must produce the production SPA at `web_ui/dist/app/` (including `index.html` and assets), with the build hook able to reproduce it. The current CI script has **no separate hard byte-size threshold**; Vite chunk-size warnings are not a passing/failing ratchet.

The frontend suite contains these additional **load-bearing golden/performance guards**:

- `src/app/canvas/renderCountGate.test.tsx`: on a 10-node canvas, a single-node update stays at **≤4 React commits and ≤1 node-view render**; position-only changes render **0** content views and a content edit renders **exactly 1**. Preserve memo comparators and stable store/dispatcher props instead of relaxing the budgets.
- `src/lib/no-raw-colors.test.ts`: hand-authored `src/app/**/*.css` declarations must use `--gl-*` tokens, except the **exact** existing pinned `rgba(...)` inventory in `app/styles.css`; hand-authored TSX `style={{...}}` blocks must have **zero** hardcoded hex/rgba literals. If removing a pinned exception, lower the inventory; do not add new raw colors.
- `src/lib/tokens/token-theme-contrast.test.ts`: dark/light token-name parity, identical two light-mode blocks, and accessible contrast must hold. Normal/primary text roles use **7:1 (AAA)**; muted/semantic roles use **4.5:1 (AA)** on the worst-case field surface. Primary text on body surfaces and composer input text must meet **7:1**.
- `src/lib/bridge-core/wireTypeCastGuard.test.ts`: never extend generated wire row types through local intersections or escape via `as unknown as GeneratedRowType`; add the real field to the Python contract and regenerate instead.
- `src/lib/ws/queueableClassificationGuard.test.ts`: no **non-idempotent `toggle*` intent** may opt into offline replay with `queueable: true`. Keep all intent call sites discoverable and maintain the pinned settings/cloud-provider classifications when changes are intentional.
- Other Vitest suites (including schema compatibility, chart publication parity and scientific/interaction property tests) are equally required; adjust golden fixtures only for reviewed, intentional product changes.

**npm security audit:** `audit-with-retry.mjs` blocks on unaccepted **high/critical** npm advisories, not moderate/low ones. A named advisory is accepted only while its documented exception is unexpired (currently `GHSA-fc5q-39xc-vjpf` for `picomatch`, expiring **2026-12-18**); any other severe advisory, or the expired one, fails. A registry outage is retried **three times**; if no usable report arrives, the wrapper emits an explicit CI **warning** and exits successfully **without claiming the scan was clean**. Repair the dependency set rather than bypassing findings.

## Build check: shipped artifact, not source-tree imports

The independent Ubuntu `build-check` job installs Python 3.12, Node 24, `build==1.5.0` and `twine==7.0.0`. It runs `python -m build` to create **sdist and wheel** (the build hook compiles/bundles the SPA and built-in plugins), then `python -m twine check dist/*`. To pass, the built distribution must have valid metadata/version, and its wheel must install into a new virtualenv with locked production dependencies. CI installs the wheel with `--no-deps` after the lock, changes to `/tmp` so the checkout is **not** on `sys.path`, then verifies:

- `backend.agents`, `graphlink_note_agent` and `graphlink_process_env` import from the installed wheel (all shipped root modules must be packaged).
- A `graphlink` GUI-script entry point exists, the installed SPA contains `index.html` and nonempty `assets/`, `SPA_DIST_DIR` resolves to that SPA, and the app icon exists.
- Bundled plugins are discoverable with built-in actions: at least `artifact`, `code_sandbox`, `conversation_node`, `gitlink`, `html_renderer`, `review_lens`, `system_prompt` and `web_research`. The installed plugin directory exists.
- `backend.app.create_app(...)` starts with an explicitly supplied test auth token and isolated temporary settings/chat paths.

A successful `pip install -r requirements.txt` or an import while standing in the repository is **not** evidence that the wheel passes. Keep `pyproject.toml`, `setup.py`, `MANIFEST.in`, plugin manifests and frontend build outputs aligned when changing packaging.

## File permissions: independent Ubuntu job

`posix-permissions` installs the locked dependencies and pinned pytest/xdist/Hypothesis, then uses `-n0 -m posix_permissions` against **these six files only**: `backend/tests/test_chat_library.py`, `test_db_backup.py`, `test_knowledge_store.py`, `test_backend_secrets_at_rest.py`, `test_mcp_server.py` and `test_instance_lock.py` (each with the `backend/tests/` prefix). Tests require **mode `0600`** for chat databases and SQLite WAL/SHM sidecars, knowledge DBs, database backups and restored files, `session.dat` and corrupted-data backups, headless MCP token files, and data-directory lock files. They also verify repair of previously loose permissions (e.g. `0644` or `0666`). Preserve privacy on creation **and** reuse/recovery. Windows checks platform-specific DPAPI/secrets/ACL behavior in `python-tests`; it does not substitute for POSIX mode assertions. When adding a marked test file, update the CI file list as described above.

## Optional and scheduled checks (do not confuse with PR blockers)

- **Opt-in browser E2E:** manually dispatch `CI` with `run_browser_tests=true`. On Ubuntu, install the Python lock and `npm ci`, build `web_ui/dist/app`, run `npx playwright install --with-deps chromium`, then from `web_ui/` run **`npm run test:e2e`** and **`npm run test:e2e:safety`**. Both drive real backend/SPA wiring in Chromium (single worker; no mock server). The safety suite uses an ephemeral auth token and captures approval/review evidence in `web_ui/test-results/` (CI uploads it even on failure).
- **Daily wall-clock performance:** `.github/workflows/nightly-perf.yml` runs on Windows at 09:00 UTC or on demand, invoking `python -m backend.tests.perf.check_baseline`. The committed `backend/tests/perf/baseline.json` must contain the same workload names as `graph_factory.ALL_WORKLOADS`. For timing baselines **≥1 ms**, a **≥20% slowdown fails** (≥100%/2× is called out as severe). For deterministic snapshot/payload sizes, **≥10% absolute drift fails**. `fixture_build_ms` is **report-only**; sub-1-ms timing baselines are not percentage-gated. Fix real regressions, or deliberately re-measure and review a baseline update with `python -m backend.tests.perf.measure_baselines`; do not silently overwrite it.
- **Weekly mutation testing:** `.github/workflows/nightly-mutation.yml` runs on Ubuntu on Mondays at 09:00 UTC or on demand. `mutmut` targets `graphlink_wire_schema.py`, `graphlink_migrations.py`, `graphlink_chart_data.py` and `backend/secret_scrub.py`, using `mutation_tests/`. Stryker targets the four TS modules in `web_ui/stryker.conf.mjs`. These jobs **report scores without a numeric mutation-score floor**, but require working CLIs, successful test execution, fresh/nonempty reports and at least one evaluated mutant; empty/incomplete runs fail. `mutmut` needs Unix `fork()` and is not a native Windows development dependency.

## PR discipline

Never commit directly to `main`. Use a focused `agent/<short-kebab-slug>` branch for agent-authored changes, push the branch, and open a PR targeting `main` using `.github/pull_request_template.md`. Describe the changed behavior, affected CI contracts/ratchets, any deliberate golden updates and exactly which validation commands you ran (or could not run). Include UI evidence for visible changes. Prefer fixing a failing constraint to disabling the relevant job or quietly raising its baseline.
