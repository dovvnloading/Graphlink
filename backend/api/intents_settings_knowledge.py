"""WebSocket intents backing Settings > Knowledge.

The model scanner uses the running Ollama server's installed models, not
filesystem manifests. All probing, testing, and disk persistence run in a
worker thread so the settings WebSocket never blocks other sessions.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import api_provider

from backend.api._settings_shared import SettingsSessionState, run_locked
from backend.events import SessionBus
from backend.knowledge_embedding_config import test_embedding_choice
from graphlink_settings_store import SettingsManager


def register_settings_knowledge_intents(
    bus: SessionBus, manager: SettingsManager, state: SettingsSessionState,
    *, on_embedding_changed: Callable[[], Awaitable[object]] | None = None,
) -> None:
    async def scan_knowledge_ollama_models() -> None:
        if state.knowledge_scan_status == "running":
            return
        state.knowledge_scan_status = "running"
        state.knowledge_notice = ""
        await bus.publish("app-settings")

        def scan() -> tuple[list[str], list[str]]:
            descriptors, reachable, error = api_provider._list_model_descriptors_from_running_ollama()
            if not reachable:
                raise RuntimeError(f"Ollama is unavailable: {error}")
            installed = sorted({item.model_id for item in descriptors}, key=str.casefold)
            capable = {name for name in installed if api_provider.ollama_supports_embedding(name)}
            ordered = sorted(
                installed,
                key=lambda name: (
                    name not in capable,
                    not name.casefold().startswith("nomic-embed-text"),
                    name.casefold(),
                ),
            )
            return ordered, [name for name in ordered if name in capable]

        try:
            models, capable_models = await asyncio.to_thread(scan)
        except Exception as exc:
            state.knowledge_scan_status = "error"
            state.knowledge_notice = f"Could not list installed Ollama models: {exc}"
        else:
            state.knowledge_ollama_models = models
            state.knowledge_ollama_capable_models = capable_models
            state.knowledge_scan_status = "done"
            if not capable_models:
                state.knowledge_notice = "No embedding-capable Ollama models installed. Try pulling nomic-embed-text."
        await bus.publish("app-settings")

    async def set_knowledge_embedding(
        source: str, model: str = "", cloud_provider: str = "", cloud_opt_in: bool = False,
    ) -> None:
        state.knowledge_notice = ""
        previous_choice = manager.get_knowledge_embedding_settings()
        try:
            if source == "ollama" and not await asyncio.to_thread(api_provider.ollama_supports_embedding, model):
                raise ValueError("Select an installed Ollama model that supports embeddings.")
            await asyncio.to_thread(
                run_locked, manager.set_knowledge_embedding_settings,
                source, model, cloud_provider, cloud_opt_in,
            )
        except (ValueError, OSError) as exc:
            state.knowledge_notice = f"Could not save knowledge settings: {exc}"
        else:
            state.knowledge_test_status = "idle"
            state.knowledge_notice = "Knowledge embedding source saved."
            if (
                on_embedding_changed is not None
                and manager.get_knowledge_embedding_settings() != previous_choice
            ):
                # Settings are persisted before the worker is scheduled; the
                # current workspace is resolved by the live knowledge intent.
                # Scheduling itself never probes or embeds on this loop.
                await on_embedding_changed()
        await bus.publish("app-settings")

    async def test_knowledge_embedding(
        source: str, model: str = "", cloud_provider: str = "", cloud_opt_in: bool = False,
    ) -> None:
        if state.knowledge_test_status == "running":
            return
        state.knowledge_test_status = "running"
        state.knowledge_notice = ""
        await bus.publish("app-settings")
        try:
            dims = await asyncio.to_thread(
                test_embedding_choice, manager, source, model, cloud_provider, cloud_opt_in,
            )
        except Exception as exc:
            state.knowledge_test_status = "error"
            if source == "cloud" and cloud_opt_in is True:
                # SDK errors can quote environment-sourced keys as well as
                # stored keys. Never send their raw messages to the UI.
                state.knowledge_notice = (
                    "Embedding test failed. Check the API endpoint, credential, and embedding model ID."
                )
            else:
                state.knowledge_notice = "Embedding test failed: " + str(exc)[:400]
        else:
            state.knowledge_test_status = "done"
            state.knowledge_notice = (
                f"Embedding test succeeded ({dims} dimensions). No stored document text was sent."
                if dims else "Keyword search is ready; no embedding model or network call is needed."
            )
        await bus.publish("app-settings")

    bus.register_intent("app-settings", "scanKnowledgeOllamaModels", scan_knowledge_ollama_models)
    bus.register_intent("app-settings", "setKnowledgeEmbedding", set_knowledge_embedding)
    bus.register_intent("app-settings", "testKnowledgeEmbedding", test_knowledge_embedding)
