# Contributing to Graphlink

Thanks for contributing to Graphlink.

## Before You Start

- Use **Graphlink** for the product name and `graphlink` for Python modules.
- The app is currently developed primarily on Windows.
- The repository is script-oriented, so the launch working directory matters.

## Local Setup

Use Python 3.12 and Node.js 24 to match CI. Open PowerShell in the repository root.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install pytest pytest-cov pytest-xdist hypothesis ruff mypy
npm --prefix web_ui ci
npm --prefix web_ui run build
python graphlink_desktop.py
```

The second pip command installs the development tools listed in the project's `dev` extra. The runtime lock alone omits pytest and its required plugins. The frontend build produces `web_ui/dist/app/index.html`. Rebuild after editing the UI, then relaunch the desktop app to try the change. An installed wheel includes the built frontend.

Windows uses WebView2. For Linux GTK/WebKitGTK or macOS Cocoa prerequisites, follow the [platform GUI installation guide](docs/PLATFORM_GUI_INSTALL.md).

To inspect a workspace without a desktop window, follow the [headless backend and MCP guide](docs/HEADLESS_MCP.md). It covers the loopback server, scoped access tokens and MCP client setup.

## Development Rules

- Launch the app from the repo root (`python graphlink_desktop.py`), not from a subdirectory.
- Prefer editing the real implementation modules in:
  - `graphlink_plugins/` (repo root - Qt-free plugin domain logic: `web_research/`, `gitlink/`, `pycoder/`, `code_sandbox/`, `common/`)
  - `backend/domain/` (the node/graph/connection domain model - chat, code, document, thinking, html, image, conversation, web_research, artifact, gitlink, pycoder, code_sandbox, note, frame/container, and chart node kinds all live here, with no UI code; purity gated by `tests/test_domain_purity.py`) and `backend/canvas.py` (the scene/grid topic + intent wiring on top of it)
  - `web_ui/src/app/` (the actual UI - `canvas/` for the React Flow graph surface, `chrome/` for the app bar and composer, `overlays/` for dialogs and popovers)
- Keep changes focused. UI cleanup, plugin behavior, persistence updates, and provider changes should be easy to review independently.

## Git & GitHub Workflow

The standing branch → push → PR → merge process for this repository. It codifies what the commit history already does in practice, so it does not have to be re-derived each time.

- **Never commit directly to `main`.** Every change gets a topic branch.
- **Branch naming:** `agent/<short-kebab-slug>` for AI-agent-authored work (Claude Code, Codex, or similar), or a short descriptive kebab-case slug for human-authored branches. Examples from history: `agent/composer-react-qwebengine`, `agent/sota-model-settings`, `codex/navigation-pins-refactor`.
- **Scope each branch to one reviewable unit of work** — the same "keep changes focused" rule from Development Rules, applied at the branch level. Large multi-part efforts should land as a sequence of scoped branches and PRs rather than one oversized branch.
- **Push the branch to `origin`**, then open a pull request against `main` using `.github/pull_request_template.md`.
- **Run local validation before opening the PR** (see [Validation](#validation)).
- **Merge strategy: squash-merge into `main`**, then delete the branch. Merged commit titles carry the PR number, e.g. `Refactor navigation pins and interaction surfaces (#17)`.

## Pull Request Expectations

Please include:

- A clear summary of the problem being solved.
- A concise explanation of the implementation approach.
- Notes about any architectural tradeoffs.
- Screenshots or short recordings for visible UI changes.
- Manual verification steps.

## Validation

With the development environment active, run these checks from the repository root. The performance tests run separately so parallel workers and coverage tracing do not affect their timing.

```powershell
python -m compileall -q backend contracts plugins
python -m ruff check .
python -m mypy
python -m pytest -q --cov --ignore=backend/tests/perf
python -m pytest -q -n0 backend/tests/perf
npm --prefix web_ui run check
```

The frontend check verifies generated schemas, TypeScript types, lint, Vitest tests, the production build and its bundle-size limit. The [CI workflow](.github/workflows/ci.yml) runs Python checks on Windows, including DPAPI tests. Ubuntu runs frontend checks, an isolated wheel installation and POSIX permission tests. For browser coverage, dispatch **CI** manually with **Run optional Chromium browser tests** enabled to run the Playwright suites against the built app.

For changes that affect the desktop experience, check the workflow in the native app.

1. Launch the app successfully.
2. Create or load a chat session.
3. Exercise the area you changed.
4. Verify the app still saves and reloads without obvious breakage.

## Good First Areas

These areas are especially valuable for contributors:

- UI consistency and polish
- plugin ergonomics
- cross-platform cleanup
- settings and secret-storage improvements
- test coverage and CI expansion
- documentation

## Reporting Issues

Use the GitHub issue templates when possible:

- Bug report for defects or regressions
- Feature request for product and workflow improvements

If the issue is security-sensitive, avoid posting exploit details publicly first.
