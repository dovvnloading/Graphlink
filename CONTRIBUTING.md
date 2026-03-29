# Contributing to Graphlink

Thanks for contributing to Graphlink.

## Before You Start

- The UI product name is **Graphlink**, but many modules and folders still use **Graphite**.
- The app is currently developed primarily on Windows.
- The repository is script-oriented, so the launch working directory matters.

## Local Setup

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
cd graphite_app
python graphite_app.py
```

If you prefer Visual Studio, open `graphite_app.sln`.

## Development Rules

- Launch the app from `graphite_app/`, not from the repo root.
- Prefer editing the real implementation modules in:
  - `graphite_app/graphite_plugins/`
  - `graphite_app/graphite_nodes/`
  - `graphite_app/graphite_canvas/`
  - `graphite_app/graphite_ui_dialogs/`
- Treat top-level wrapper modules such as `graphite_plugin_code_review.py` as compatibility facades unless the change is specifically about import stability.
- Keep changes focused. UI cleanup, plugin behavior, persistence updates, and provider changes should be easy to review independently.

## Pull Request Expectations

Please include:

- A clear summary of the problem being solved.
- A concise explanation of the implementation approach.
- Notes about any architectural tradeoffs.
- Screenshots or short recordings for visible UI changes.
- Manual verification steps.

## Manual Validation Checklist

There is not yet a broad automated test suite, so please do at least the following when relevant:

1. Launch the app successfully.
2. Create or load a chat session.
3. Exercise the area you changed.
4. Verify the app still saves and reloads without obvious breakage.
5. Run a Python compile smoke check:

```powershell
python -m compileall -q graphite_app
```

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
