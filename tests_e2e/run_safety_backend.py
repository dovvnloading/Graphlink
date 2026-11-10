"""Authenticated, disposable backend for approval/review browser evidence.

This entry point alone installs the fixture routes in safety_fixtures.py.
Production create_app has no fixture routes. The only provider is a scripted
offline sequence; approval, validation, dispatch, PythonREPL, and disk writes
are the real implementation. See docs/APPROVAL_REVIEW_EVIDENCE.md.
"""

from __future__ import annotations

import os
import importlib
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    token = os.environ.pop("GRAPHLINK_SAFETY_E2E_TOKEN", "")
    if not token:
        raise RuntimeError("The dedicated Playwright safety config must supply its ephemeral token.")
    # The app process itself, not only its Python child, must not inherit live
    # provider/cloud credentials or Git routing. Reuse the production explicit
    # allowlist and preserve POSIX's caller-chosen temporary directory too.
    from graphlink_process_env import safe_subprocess_env

    clean_environment = safe_subprocess_env()
    if "TMPDIR" in os.environ:
        clean_environment["TMPDIR"] = os.environ["TMPDIR"]
    os.environ.clear()
    os.environ.update(clean_environment)

    with tempfile.TemporaryDirectory(prefix="graphlink-browser-safety-") as directory:
        root = Path(directory)
        # Isolate the real scratch-directory implementation as well as settings
        # and fixtures. Assign before modules derive their scratch constants.
        tempfile.tempdir = str(root)

        import uvicorn
        from unittest.mock import patch

        import graphlink_token_estimator as token_estimator

        # Provider discovery may probe a local Ollama service. It is outside the
        # claim and disabled; token counting uses the app's existing character
        # fallback so tiktoken cannot download an encoding during a cold run.
        with ExitStack() as isolation:
            isolation.enter_context(patch.object(token_estimator, "TIKTOKEN_AVAILABLE", False))
            # The chat library's autosave indexes into a SECOND database. Its
            # default is independent of create_app(chat_db_path=...), so isolate
            # it before importing modules that capture DEFAULT_DB_PATH aliases.
            from backend import knowledge_store

            knowledge_db = root / "knowledge" / "knowledge.db"
            isolation.enter_context(patch.object(knowledge_store, "DEFAULT_DB_PATH", knowledge_db))
            from backend import app as app_module
            from tests_e2e.safety_fixtures import SafetyFixtures

            # Also update aliases if this entry point is invoked in an already
            # imported test process. Real autosave/search/ingest remain intact.
            for module_name, attribute in (
                ("backend.tools_knowledge", "DEFAULT_DB_PATH"),
                ("backend.knowledge_ingest", "DEFAULT_DB_PATH"),
                ("backend.api.intents_knowledge", "DEFAULT_DB_PATH"),
                ("backend.api.intents_global_search", "DEFAULT_DB_PATH"),
                ("backend.api.intents_web_research", "KNOWLEDGE_DEFAULT_DB_PATH"),
            ):
                isolation.enter_context(patch.object(importlib.import_module(module_name), attribute, knowledge_db))
            from backend import chat_library

            isolation.enter_context(patch.object(chat_library, "DEFAULT_DB_PATH", root / "chats.db"))
            for module_name, attribute, dirname in (
                ("graphlink_scratch_dirs", "HARNESS_WORKSPACE_ROOT", "harness"),
                ("graphlink_scratch_dirs", "PYTHON_REPL_ROOT", "python-repls"),
                ("graphlink_scratch_dirs", "EXECUTION_SANDBOX_ROOT", "execution"),
                ("backend.harness.workspace", "HARNESS_WORKSPACE_ROOT", "harness"),
                ("backend.api.intents_nodes", "HARNESS_WORKSPACE_ROOT", "harness"),
                ("backend.tools_graph", "HARNESS_WORKSPACE_ROOT", "harness"),
                ("backend.agents", "EXECUTION_SANDBOX_ROOT", "execution"),
                ("graphlink_plugins.common.python_repl", "PYTHON_REPL_ROOT", "python-repls"),
                ("graphlink_plugins.code_sandbox.domain", "EXECUTION_SANDBOX_ROOT", "execution"),
            ):
                isolation.enter_context(patch.object(importlib.import_module(module_name), attribute, root / dirname))
            # Plugin manifests come from this source checkout; their grants use
            # the explicitly supplied settings file. create_app does not invoke
            # desktop configure_logging/exception-handler setup or token files.
            isolation.enter_context(patch.object(app_module, "bootstrap_provider_state", lambda _settings: None))
            app = app_module.create_app(
                spa_dir=REPO_ROOT / "web_ui" / "dist" / "app",
                settings_state_file=root / "session.dat",
                chat_db_path=root / "chats.db",
                auth_token=token,
            )
            fixtures = SafetyFixtures(app, root, REPO_ROOT)
            with fixtures.installed():
                uvicorn.run(app, host="127.0.0.1", port=8801, log_level="warning")


if __name__ == "__main__":
    main()
