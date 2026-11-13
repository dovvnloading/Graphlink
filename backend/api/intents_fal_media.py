"""Fal Media Studio's first-class, session-scoped scene intents.

Creation uses the built-in Plugin SDK picker action. Edits go through the
undoable command layer; catalog queries and remote queue operations are
non-undoable lifecycle actions. No API key reaches the WebSocket.
"""
from __future__ import annotations

from backend.domain.node_access import require_node
from backend.domain.node_states import FalMediaState
from backend.fal_media_domain import read_media, store_media, validate_edit
from backend.fal_media_controller import FalMediaController


def register_fal_media_intents(bus, document, settings_manager) -> None:
    controller = FalMediaController(bus, document, settings_manager)

    async def save_fal_media_draft(
        node_id: str, mode: str, operation: str, model_id: str,
        model_name: str, prompt: str, arguments_json: str,
    ) -> None:
        node = require_node(document.nodes, node_id, "fal_media", FalMediaState)
        current = read_media(node)
        updated = validate_edit(current, mode, operation, model_id,
                                model_name, prompt, arguments_json)
        if updated == current:
            return
        old_model, old_operation = current["modelId"], current["operation"]

        def commit():
            store_media(node, updated)
            if model_id != old_model:
                node.state.schema_json = ""
            if operation != old_operation:
                node.state.catalog_json = ""

        document.record_command("saveFalMediaDraft", "user", commit, node_ids=[node_id])
        await bus.publish("scene")

    async def search_fal_media_catalog(node_id: str, mode: str, operation: str,
                                       query: str = "", cursor: str = "") -> None:
        await controller.search(node_id, mode, operation, query, cursor)

    async def inspect_fal_media_model(node_id: str, model_id: str) -> None:
        await controller.inspect(node_id, model_id)

    async def set_fal_media_favorite(node_id: str, model_id: str, enabled: bool) -> None:
        await controller.set_favorite(node_id, model_id, enabled)

    async def prepare_fal_media(
        node_id: str, mode: str, operation: str, model_id: str,
        model_name: str, prompt: str, arguments_json: str,
    ) -> None:
        # Capture EXACTLY the inputs the user reviewed. Never trust a
        # potentially-racy preceding saveDraft intent's old state.
        node = require_node(document.nodes, node_id, "fal_media", FalMediaState)
        current = read_media(node)
        updated = validate_edit(current, mode, operation, model_id,
                                model_name, prompt, arguments_json)
        if updated != current:
            old_model = current["modelId"]
            def commit():
                store_media(node, updated)
                if model_id != old_model:
                    node.state.schema_json = ""
            document.record_command("prepareFalMedia", "user", commit, node_ids=[node_id])
            await bus.publish("scene")
        await controller.prepare(node_id)

    async def generate_fal_media(node_id: str, approval_id: str) -> None:
        await controller.generate(node_id, approval_id)

    async def refresh_fal_media(node_id: str) -> None:
        await controller.refresh(node_id)

    async def acknowledge_fal_media_submission(node_id: str) -> None:
        await controller.acknowledge_unconfirmed(node_id)

    async def cancel_fal_media(node_id: str) -> None:
        await controller.cancel(node_id)

    bus.register_intent("scene", "saveFalMediaDraft", save_fal_media_draft)
    bus.register_intent("scene", "searchFalMediaCatalog", search_fal_media_catalog)
    bus.register_intent("scene", "inspectFalMediaModel", inspect_fal_media_model)
    bus.register_intent("scene", "setFalMediaFavorite", set_fal_media_favorite)
    bus.register_intent("scene", "prepareFalMedia", prepare_fal_media)
    bus.register_intent("scene", "generateFalMedia", generate_fal_media)
    bus.register_intent("scene", "refreshFalMedia", refresh_fal_media)
    bus.register_intent("scene", "cancelFalMedia", cancel_fal_media)
    bus.register_intent("scene", "acknowledgeFalMediaSubmission", acknowledge_fal_media_submission)
