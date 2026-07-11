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

## Development Rules

- Launch the app from `graphlink_app/`, not from the repo root.
- Prefer editing the real implementation modules in:
  - `graphlink_app/graphlink_plugins/`
  - `graphlink_app/graphlink_nodes/`
  - `graphlink_app/graphlink_canvas/`
  - `graphlink_app/graphlink_ui_dialogs/`
- Treat top-level wrapper modules such as `graphlink_plugin_gitlink.py` as compatibility facades unless the change is specifically about import stability.
- Keep changes focused. UI cleanup, plugin behavior, persistence updates, and provider changes should be easy to review independently.

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
