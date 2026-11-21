"""Session-only Ollama Check/Cancel intents, separate from settings writes."""

from __future__ import annotations

import asyncio

from backend.api._settings_shared import SettingsSessionState
from backend.events import SessionBus
from backend.ollama_model_check import probe_ollama_model
from backend.ollama_inventory import refresh_ollama_loaded_state
from backend.ollama_model_names import valid_ollama_model_name


def register_ollama_model_check_intents(bus: SessionBus, state: SettingsSessionState) -> None:
    async def check_ollama_model(model_name: str) -> None:
        if not isinstance(model_name, str):
            return
        model_name = model_name.strip()
        # A disk manifest, stale snapshot, or forged WS argument is not
        # authorization to load a model (and potentially allocate gigabytes).
        if (not valid_ollama_model_name(model_name)
                or state.ollama_server_reachable is not True
                or state.ollama_inventory_source != "server"):
            return
        if not any(row.get("name") == model_name and row.get("source") == "server"
                   for row in state.ollama_inventory):
            return
        if (state.ollama_scan_status == "running"
                or state.ollama_pull_status in ("running", "cancelling", "finalizing")
                or state.ollama_remove_status == "running"):
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
            # Published snapshots contain references to their Check rows.
            # Keep the running row untouched until /api/ps is refreshed and
            # the active-task gate is released. Otherwise a recorder or
            # buffered send can observe "complete"/"cancelled" too early.
            final_report = dict(report)
            try:
                result = await probe_ollama_model(model_name)
                for key in ("loadSeconds", "tokensPerSecond", "toolCallingWorks", "message"):
                    final_report[key] = result.get(key)
                final_report["status"] = "complete"
            except asyncio.CancelledError:
                final_report["status"] = "cancelled"
                final_report["message"] = "Check cancelled."
            except Exception as exc:  # noqa: BLE001 - report daemon failures to the user
                final_report["status"] = "error"
                detail = str(exc).strip()[:240] or "Ollama did not respond"
                final_report["message"] = (
                    f"Could not check {model_name}: {detail}. "
                    "Check Ollama status, then retry."
                )
            finally:
                # The probe can change Ollama's residency. Refresh /api/ps
                # after success, error or cancellation; do not re-scan /api/show
                # or mistake a failed /api/ps query for an unloaded model.
                try:
                    if state.ollama_inventory_source == "server":
                        state.ollama_inventory = await asyncio.to_thread(
                            refresh_ollama_loaded_state, state.ollama_inventory,
                        )
                except asyncio.CancelledError:
                    # Cancellation may also arrive during the off-loop refresh.
                    final_report["status"] = "cancelled"
                    final_report["message"] = "Check cancelled."
                except Exception:
                    pass  # A transient /api/ps failure must not hide the Check report.
                finally:
                    if state.ollama_check_task is asyncio.current_task():
                        state.ollama_check_task = None
                        state.ollama_check_name = ""
                    # Replace, never mutate, the previously published row.
                    # A terminal status is observable only once residency
                    # and the cancellation/restart gate are both settled.
                    state.ollama_model_checks[model_name] = final_report
                    await bus.publish("app-settings")

        # Return from the intent immediately: cancellation arrives through
        # another intent while the async Ollama request is still pending.
        task = asyncio.create_task(run_probe())

        def recover_prestart_cancellation(done: asyncio.Task[None]) -> None:
            # A task cancelled before its first instruction never reaches
            # run_probe's try/finally. Restore the session gate in that case.
            if done.cancelled() and state.ollama_check_task is done:
                state.ollama_model_checks[model_name] = {
                    **report, "status": "cancelled", "message": "Check cancelled.",
                }
                state.ollama_check_task = None
                state.ollama_check_name = ""
                asyncio.create_task(bus.publish("app-settings"))

        state.ollama_check_task = task
        task.add_done_callback(recover_prestart_cancellation)
        await bus.publish("app-settings")

    async def cancel_ollama_model_check(model_name: str) -> None:
        task = state.ollama_check_task
        if isinstance(model_name, str) and model_name == state.ollama_check_name and task is not None:
            if not task.done():
                task.cancel()

    bus.register_intent("app-settings", "checkOllamaModel", check_ollama_model)
    bus.register_intent("app-settings", "cancelOllamaModelCheck", cancel_ollama_model_check)
