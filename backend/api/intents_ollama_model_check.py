"""Session-only Ollama Check/Cancel intents, separate from settings writes."""

from __future__ import annotations

import asyncio

from backend.api._settings_shared import SettingsSessionState
from backend.events import SessionBus
from backend.ollama_model_check import probe_ollama_model


def register_ollama_model_check_intents(bus: SessionBus, state: SettingsSessionState) -> None:
    async def check_ollama_model(model_name: str) -> None:
        if not isinstance(model_name, str):
            return
        model_name = model_name.strip()
        # Only probe models from the SAME inventory used by Settings/Knowledge.
        # Never allow an arbitrary model ID to trigger a download or inference.
        if not model_name or not any(row.get("name") == model_name for row in state.ollama_inventory):
            return
        if state.ollama_check_task is not None and not state.ollama_check_task.done():
            return

        report: dict = {
            "name": model_name,
            "status": "running",
            "loadSeconds": None,
            "tokensPerSecond": None,
            "toolCallingWorks": None,
            "message": "",
        }
        state.ollama_model_checks[model_name] = report
        state.ollama_check_name = model_name

        async def run_probe() -> None:
            try:
                report.update(await probe_ollama_model(model_name))
                report["status"] = "complete"
            except asyncio.CancelledError:
                report["status"] = "cancelled"
                report["message"] = "Check cancelled."
            except Exception as exc:  # noqa: BLE001 - report daemon failures to the user
                report["status"] = "error"
                report["message"] = f"Check failed: {str(exc)[:240]}"
            finally:
                state.ollama_check_task = None
                state.ollama_check_name = ""
                await bus.publish("app-settings")

        # Return from the intent immediately: cancellation arrives through
        # another intent while the async Ollama request is still pending.
        state.ollama_check_task = asyncio.create_task(run_probe())
        await bus.publish("app-settings")

    async def cancel_ollama_model_check(model_name: str) -> None:
        task = state.ollama_check_task
        if isinstance(model_name, str) and model_name == state.ollama_check_name and task is not None:
            if not task.done():
                task.cancel()

    bus.register_intent("app-settings", "checkOllamaModel", check_ollama_model)
    bus.register_intent("app-settings", "cancelOllamaModelCheck", cancel_ollama_model_check)
