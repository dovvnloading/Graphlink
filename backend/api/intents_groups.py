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

    # ADR-010 stage 10.1: the create/delete-shaped intents here are wrapped
    # in record_command (see backend/domain/commands.py). The pure setters
    # below (setNoteContent, setGroupLabel, toggle*, resize*) are NOT - they
    # are stage 10.1's explicit scope boundary, which covers create/delete/
    # move/connect. Every one of them still needs a command before ADR-010
    # can close, per that ADR's own Consequences section ("Every new
    # mutating intent ... must produce a command").
    async def add_note(x, y, is_system_prompt=False, is_summary_note=False):
        # The palette's "Add Note" always sends the viewport center, so a
        # second note would land exactly on the first - collision-resolve
        # the requested spot (a no-op when it is already clear).
        width, height = document.kind_fallback_footprint("note")
        nx, ny = document.find_free_position(float(x), float(y), width, height)
        node, _command = document.record_command(
            "addNote", "user",
            lambda: document.add_note(
                nx, ny, is_system_prompt=is_system_prompt, is_summary_note=is_summary_note,
            ),
        )
        await publish_scene()
        return node.id

    async def set_note_content(node_id, content):
        document.record_command(
            "setNoteContent", "user", lambda: document.set_note_content(node_id, content),
            node_ids=[node_id],
        )
        await publish_scene()

    async def create_frame(item_ids):
        ids = list(item_ids)
        # The members are named so their own detach-from-a-previous-group
        # mutation is captured too - create_frame does not only create the
        # frame node, it also rewrites each member's existing membership
        # (see _detach_from_existing_group).
        node, _command = document.record_command(
            "createFrame", "user", lambda: document.create_frame(ids),
            node_ids=ids,
        )
        await publish_scene()
        return node.id

    async def create_container(item_ids):
        ids = list(item_ids)
        node, _command = document.record_command(
            "createContainer", "user", lambda: document.create_container(ids),
            node_ids=ids,
        )
        await publish_scene()
        return node.id

    async def set_group_label(node_id, text):
        document.record_command(
            "setGroupLabel", "user", lambda: document.set_group_label(node_id, text),
            node_ids=[node_id],
        )
        await publish_scene()

    async def set_group_color(node_id, color, header_color):
        document.record_command(
            "setGroupColor", "user",
            lambda: document.set_group_color(node_id, color, header_color),
            node_ids=[node_id],
        )
        await publish_scene()

    # toggle_frame_lock/toggle_group_collapsed FLIP a boolean rather than
    # setting it - record_command's snapshot/restore is safe for this
    # regardless (it restores the captured object, never re-invokes the
    # mutator), unlike ADR-003's offline-queue replay mechanism, which is
    # why those two intents are marked non-queueable on the frontend side
    # while being perfectly fine to wrap here.
    async def toggle_frame_lock(node_id):
        document.record_command(
            "toggleFrameLock", "user", lambda: document.toggle_frame_lock(node_id),
            node_ids=[node_id],
        )
        await publish_scene()

    async def toggle_group_collapsed(node_id):
        document.record_command(
            "toggleGroupCollapsed", "user", lambda: document.toggle_group_collapsed(node_id),
            node_ids=[node_id],
        )
        await publish_scene()

    async def resize_frame(node_id, width, height):
        document.record_command(
            "resizeFrame", "user", lambda: document.resize_frame(node_id, width, height),
            node_ids=[node_id],
        )
        await publish_scene()

    async def fit_frame_to_content(node_id):
        document.record_command(
            "fitFrameToContent", "user", lambda: document.fit_frame_to_content(node_id),
            node_ids=[node_id],
        )
        await publish_scene()

    async def ungroup(node_id):
        # A delete, structurally: the group wrapper node is removed while
        # its members are released rather than cascade-deleted.
        document.record_command(
            "ungroup", "user", lambda: document.ungroup(node_id),
            node_ids=[node_id],
        )
        await publish_scene()

    async def report_node_sizes(sizes):
        # The frontend's continuous "here is what these nodes actually
        # render as" report - the only way this backend can learn a chat
        # node's real height (see SceneDocument.measured_sizes's comment).
        # Deliberately NOT a recorded command: an observation about
        # rendering is not a user edit, and putting it in the undo stack
        # would make Ctrl+Z replay layout noise.
        triples = [(s[0], s[1], s[2]) for s in sizes]
        # Republish ONLY when a group box actually moved. The client
        # re-reports steady-state sizes routinely (any re-measure), and a
        # scene snapshot per report would be pure churn.
        if document.set_measured_node_sizes(triples):
            await publish_scene()

    bus.register_intent("scene", "addNote", add_note)
    bus.register_intent("scene", "reportNodeSizes", report_node_sizes)
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
