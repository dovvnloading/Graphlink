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
cd graphlink_app
python graphlink_app.py
```

If you prefer Visual Studio, open `graphlink_app.sln`.

`python graphlink_app.py` also builds `web_ui/`'s frontend assets automatically the first time, or whenever they're stale - install [Node.js](https://nodejs.org/) 22 or newer first (22 is the enforced floor; `web_ui/.nvmrc` pins the specific newer version this project is actually developed and tested against, for anyone using `nvm`/`fnm`). No separate `npm run build` step is needed. Set `GRAPHLINK_FRONTEND_DEV=1` to skip this and launch against whatever's already built, e.g. while running `npm run dev` yourself in `web_ui/` for frontend iteration.

## Development Rules

- Launch the app from `graphlink_app/`, not from the repo root.
- Prefer editing the real implementation modules in:
  - `graphlink_app/graphlink_plugins/`
  - `graphlink_app/graphlink_nodes/`
  - `graphlink_app/graphlink_canvas/`
  - `graphlink_app/graphlink_ui_dialogs/`
- Treat top-level wrapper modules such as `graphlink_plugin_gitlink.py` as compatibility facades unless the change is specifically about import stability.
- Keep changes focused. UI cleanup, plugin behavior, persistence updates, and provider changes should be easy to review independently.

## Git & GitHub Workflow

The standing branch → push → PR → merge process for this repository. It codifies what the commit history already does in practice, so it does not have to be re-derived each time.

- **Never commit directly to `main`.** Every change gets a topic branch.
- **Branch naming:** `agent/<short-kebab-slug>` for AI-agent-authored work (Claude Code, Codex, or similar), or a short descriptive kebab-case slug for human-authored branches. Examples from history: `agent/composer-react-qwebengine`, `agent/sota-model-settings`, `codex/navigation-pins-refactor`.
- **Scope each branch to one reviewable unit of work** — the same "keep changes focused" rule from Development Rules, applied at the branch level. Large multi-part efforts should land as a sequence of scoped branches and PRs rather than one oversized branch.
- **Push the branch to `origin`**, then open a pull request against `main` using `.github/pull_request_template.md`.
- **Run local validation before opening the PR** (see Validation below). There is no automated CI — GitHub Actions and Dependabot were intentionally removed in commit `185143a` — so this local pass is the only gate.
- **Merge strategy: squash-merge into `main`**, then delete the branch. Merged commit titles carry the PR number, e.g. `Refactor navigation pins and interaction surfaces (#17)`.

## Pull Request Expectations

Please include:

- A clear summary of the problem being solved.
- A concise explanation of the implementation approach.
- Notes about any architectural tradeoffs.
- Screenshots or short recordings for visible UI changes.
- Manual verification steps.

## Validation

Run the `pytest` suite from the inner `graphlink_app/` directory, and a compile smoke check:

```powershell
cd graphlink_app
pytest
python -m compileall -q .
```

The automated coverage is headless (plugin registration, scene/session serialization, path-safety and JSON helpers, and Qt node behavior), so please also validate the app manually when relevant:

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
