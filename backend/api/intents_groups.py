"""ADR-002 stage 2.6 (PR3/3, the final slice): Notes/Frames/Containers/
Groups.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 327-374; registration calls at lines 376-386) - pure
code motion, no behavior change.
"""

from __future__ import annotations

from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.events import SessionBus


def register_groups_intents(bus: SessionBus, document: SceneDocument) -> None:
    publish_scene = make_publish_scene(bus)

    async def add_note(x, y, is_system_prompt=False, is_summary_note=False):
        node = document.add_note(
            x, y, is_system_prompt=is_system_prompt, is_summary_note=is_summary_note,
        )
        await publish_scene()
        return node.id

    async def set_note_content(node_id, content):
        document.set_note_content(node_id, content)
        await publish_scene()

    async def create_frame(item_ids):
        node = document.create_frame(list(item_ids))
        await publish_scene()
        return node.id

    async def create_container(item_ids):
        node = document.create_container(list(item_ids))
        await publish_scene()
        return node.id

    async def set_group_label(node_id, text):
        document.set_group_label(node_id, text)
        await publish_scene()

    async def set_group_color(node_id, color, header_color):
        document.set_group_color(node_id, color, header_color)
        await publish_scene()

    async def toggle_frame_lock(node_id):
        document.toggle_frame_lock(node_id)
        await publish_scene()

    async def toggle_group_collapsed(node_id):
        document.toggle_group_collapsed(node_id)
        await publish_scene()

    async def resize_frame(node_id, width, height):
        document.resize_frame(node_id, width, height)
        await publish_scene()

    async def fit_frame_to_content(node_id):
        document.fit_frame_to_content(node_id)
        await publish_scene()

    async def ungroup(node_id):
        document.ungroup(node_id)
        await publish_scene()

    bus.register_intent("scene", "addNote", add_note)
    bus.register_intent("scene", "setNoteContent", set_note_content)
    bus.register_intent("scene", "createFrame", create_frame)
    bus.register_intent("scene", "createContainer", create_container)
    bus.register_intent("scene", "setGroupLabel", set_group_label)
    bus.register_intent("scene", "setGroupColor", set_group_color)
    bus.register_intent("scene", "toggleFrameLock", toggle_frame_lock)
    bus.register_intent("scene", "toggleGroupCollapsed", toggle_group_collapsed)
    bus.register_intent("scene", "resizeFrame", resize_frame)
    bus.register_intent("scene", "fitFrameToContent", fit_frame_to_content)
    bus.register_intent("scene", "ungroup", ungroup)
