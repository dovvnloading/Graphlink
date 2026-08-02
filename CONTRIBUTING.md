# Contributing to Graphlink

Thanks for contributing to Graphlink.

## Before You Start

- The UI product name and the module/folder naming are both **Graphlink** (the codebase was renamed from its earlier **Graphite** naming).
- The app is currently developed primarily on Windows.
- The repository is script-oriented, so the launch working directory matters.

## Local Setup

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

The desktop app requires the frontend to already be built - install [Node.js](https://nodejs.org/) 22 or newer first (22 is the enforced floor; `web_ui/.nvmrc` pins the specific newer version this project is actually developed and tested against, for anyone using `nvm`/`fnm`), then:

```powershell
cd web_ui
npm install
npm run build
cd ..
```

This produces `web_ui/dist/app/index.html`. Launching the app does **not** build this automatically - if it's missing, `graphlink_desktop.py` logs an error telling you to run the build and exits with code 1.

Then launch from the repo root:

```powershell
python graphlink_desktop.py
```

This starts the Python backend (FastAPI via uvicorn) on a free localhost port in a background thread, waits for it to report healthy, then opens a single native OS webview window (WebView2 on Windows - not a browser tab) pointed at that backend. The backend serves the built frontend, the API, and the WebSocket all from that one origin.

**Iterating on frontend code:** run `npm run dev` inside `web_ui/` for a live-reloading Vite dev server (`127.0.0.1:5173`), which proxies `/api` and `/ws` requests to `http://127.0.0.1:8765`. This assumes a backend is already running on port 8765. `backend/app.py` has an opt-in `GRAPHLINK_DEV_WS_ORIGIN` env var for exactly this case - it lets a manually-run backend accept WebSocket connections from Vite's dev origin. However, there is currently no documented or scripted way to launch just the backend standalone; `graphlink_desktop.py` always boots the full backend+webview flow together. This is a known gap, not a documented workflow - if you need frontend-only iteration against a live backend, you'll have to bridge it yourself for now.

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
- **Run local validation before opening the PR** (see Validation below). `.github/workflows/ci.yml` also re-runs the same checks on every PR, but treat that as a backstop, not a substitute for running validation locally first.
- **Merge strategy: squash-merge into `main`**, then delete the branch. Merged commit titles carry the PR number, e.g. `Refactor navigation pins and interaction surfaces (#17)`.

## Pull Request Expectations

Please include:

- A clear summary of the problem being solved.
- A concise explanation of the implementation approach.
- Notes about any architectural tradeoffs.
- Screenshots or short recordings for visible UI changes.
- Manual verification steps.

## Validation

Run the Python test suite and a compile smoke check from the repo root, then the frontend checks from `web_ui/` - this matches CI exactly (`.github/workflows/ci.yml`, two jobs, both on `windows-latest`):

```powershell
python -m pytest -q
python -m compileall -q .
```

```powershell
cd web_ui
npm run check
```

`npm run check` runs schema-drift detection against `contracts/` (`codegen.py --check`), typecheck, lint, the vitest suite, and a production build, in that order.

The Python-side coverage is plain headless Python - no Qt, no offscreen platform plugin involved anymore - covering plugin registration, scene/session serialization, path-safety and JSON helpers, and the backend's canvas/domain logic. The frontend has its own real, browser-driven test suite (vitest) covering the React UI. Please also validate the app manually when relevant:

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
