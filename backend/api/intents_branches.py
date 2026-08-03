"""ADR-002 stage 2.6: note agents (Key Takeaway/Explainer) + Compare/
Synthesize Branches.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 491-713; registration calls from the former tail block
at lines 1154-1159) - pure code motion, no behavior change. Merged into one
module (not four) because all five intents share one product story - "turn
one or more existing chat branches into a new agent-authored note" - not
just a size trim.
"""

from __future__ import annotations

from backend.agents import AgentDispatcher
from backend.composer import ComposerDocument
from backend.domain.graph import SceneDocument
from backend.domain.model import (
    MESSAGE_VERTICAL_SPACING,
    NOTE_AGENT_BODY_COLOR,
    NOTE_AGENT_HEADER_COLOR,
    NOTE_AGENT_X_OFFSET,
)
from backend.events import SessionBus
from backend.notifications import NotificationState


def register_branches_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher: AgentDispatcher,
    composer_document: ComposerDocument,
) -> None:
    # Deferred import - see backend/api/intents_web_research.py's own
    # comment for why a back-reference to backend.canvas cannot be a
    # module-level import here.
    from backend.canvas import _format_branches_for_comparison

    async def _generate_note_from_node(source_node_id, note_kind, x_offset, y_offset):
        """R8a: shared path for generateKeyTakeaway and generateExplainerNote.

        Both take one chat node, run its text through an agent, and drop the
        result into a new note beside it - identical except for the agent and
        the note's offset, so they share one implementation rather than two
        that can drift.

        Source text is the node's OWN content, not the branch history that
        generate_chart uses: legacy's takeaway/explainer passed a single
        node's text, and widening that to the whole branch would change what
        the feature summarises.
        """
        if not source_node_id or source_node_id not in document.nodes:
            notifications.show("Please select a valid node first.", "warning")
            await bus.publish("notification")
            return None

        source = document.nodes[source_node_id]
        if source.kind != "chat":
            notifications.show("This node can't be summarised into a note.", "warning")
            await bus.publish("notification")
            return None
        if not source.content or not source.content.strip():
            notifications.show("The selected node has no text to summarise.", "warning")
            await bus.publish("notification")
            return None

        result_holder: dict[str, str] = {}

        async def _on_success(text):
            if source_node_id not in document.nodes:
                # Deleted mid-flight - silent no-op, same posture as
                # _dispatch_image's own liveness check.
                return
            note = document.add_note(source.x + x_offset, source.y + y_offset)
            document.set_note_content(note.id, text)
            # Legacy tinted these notes "Mid Gray" with an info-coloured
            # header. Both values come from the frontend's own palette
            # (GroupColorPicker's GROUP_MONO_COLORS/GROUP_NAMED_COLORS) since
            # the backend stores hex and never resolves a colour name. The
            # legacy note width of 400 is NOT ported: note width is not a
            # modeled field here (it is CSS-driven), so there is nothing to
            # set it on.
            document.set_group_color(note.id, NOTE_AGENT_BODY_COLOR, NOTE_AGENT_HEADER_COLOR)
            result_holder["node_id"] = note.id
            await bus.publish("scene")

        def _on_failure(message):
            # start_note_generation already surfaced the notification.
            pass

        await agent_dispatcher.start_note_generation(
            bus=bus,
            notifications_state=notifications,
            node_id=source_node_id,
            note_kind=note_kind,
            source_text=source.content,
            on_success=_on_success,
            on_failure=_on_failure,
        )
        return result_holder.get("node_id")

    async def generate_key_takeaway(source_node_id):
        return await _generate_note_from_node(source_node_id, "takeaway", NOTE_AGENT_X_OFFSET, 0)

    async def generate_explainer_note(source_node_id):
        # Offset vertically as well as horizontally so a takeaway and an
        # explainer generated from the same node don't land on top of each
        # other - the same 100px stagger legacy used.
        return await _generate_note_from_node(source_node_id, "explainer", NOTE_AGENT_X_OFFSET, 100)

    async def compare_branches(node_ids):
        """ADR-002 Workstream 1 ("Compare Branches") - the second sequenced
        item after "Branch from here" (that same workstream's fork
        primitive). Takes 2+ existing chat nodes, walks each one's own
        chat_branch_history, and drops a single agent-authored comparison
        into a new note linked back to every source branch (note.item_ids
        - see mark_branch_comparison_note).

        Deliberately no auto-selection fallback, unlike the single-node
        note agents above: there is no sensible single "the selected node"
        default here - the caller (App.tsx's own Compare Branches shortcut)
        must supply 2+ real ids up front, the same "the frontend already
        gathered React Flow's own multi-selection" contract create_frame/
        create_container already use."""
        ids = list(dict.fromkeys(str(i) for i in (node_ids or [])))  # de-dupe, preserve order
        if len(ids) < 2:
            notifications.show("Select at least 2 branches to compare.", "warning")
            await bus.publish("notification")
            return None

        sources = []
        for node_id in ids:
            node = document.nodes.get(node_id)
            if node is None or node.kind != "chat":
                notifications.show("Every selected node must be a real chat message to compare.", "warning")
                await bus.publish("notification")
                return None
            sources.append(node)

        branches = [
            (f"Branch {index + 1}", document.chat_branch_history(node.id))
            for index, node in enumerate(sources)
        ]
        formatted = _format_branches_for_comparison(branches)

        # Positioned below-and-right of the source branches, the same
        # "offset to the side" convention _generate_note_from_node uses for
        # a single source - averaged/maxed across all sources here since
        # there's more than one.
        avg_x = sum(node.x for node in sources) / len(sources)
        max_y = max(node.y for node in sources)

        result_holder: dict[str, str] = {}

        async def _on_success(text):
            if any(node_id not in document.nodes for node_id in ids):
                # A source was deleted mid-flight - same liveness posture as
                # _generate_note_from_node's own on_success guard.
                return
            note = document.add_note(avg_x + NOTE_AGENT_X_OFFSET, max_y)
            document.set_note_content(note.id, text)
            document.set_group_color(note.id, NOTE_AGENT_BODY_COLOR, NOTE_AGENT_HEADER_COLOR)
            document.mark_branch_comparison_note(note.id, ids)
            result_holder["node_id"] = note.id
            await bus.publish("scene")

        def _on_failure(message):
            # start_branch_comparison already surfaced the notification.
            pass

        await agent_dispatcher.start_branch_comparison(
            bus=bus,
            notifications_state=notifications,
            source_text=formatted,
            on_success=_on_success,
            on_failure=_on_failure,
        )
        return result_holder.get("node_id")

    async def synthesize_branches(node_ids, instructions):
        """ADR-002 Workstream 1 ("Synthesize Branches") - the third
        sequenced item in that workstream's own "fork -> compare ->
        synthesize -> status/lifecycle UI" order, following compare_
        branches above. Same validation contract as that function (2+
        de-duped ids, every one a real chat node, no auto-selection
        fallback), plus one more: instructions must be non-blank, since an
        empty steering prompt would leave the agent nothing to follow.

        Unlike Compare (whose result is a parentless note), Synthesize's
        result is a real CHAT node continuing the branch tree from the
        FIRST selected source - a genuine next step in the conversation,
        not a side annotation - so last_chat_node_id is updated to it
        exactly like an ordinary send. Every source is still recorded (via
        item_ids, the same multi-purpose-field reuse Compare's note
        already established) so full provenance survives even though only
        one edge can be structural. Provider/model are stamped from
        composer_document.route() - the same route a plain send would
        actually use - onto the result node (see ChatState's own comment,
        backend/domain/node_states.py)."""
        ids = list(dict.fromkeys(str(i) for i in (node_ids or [])))  # de-dupe, preserve order
        if len(ids) < 2:
            notifications.show("Select at least 2 branches to synthesize.", "warning")
            await bus.publish("notification")
            return None

        clean_instructions = str(instructions or "").strip()
        if not clean_instructions:
            notifications.show("Enter instructions for how to combine the branches.", "warning")
            await bus.publish("notification")
            return None

        sources = []
        for node_id in ids:
            node = document.nodes.get(node_id)
            if node is None or node.kind != "chat":
                notifications.show("Every selected node must be a real chat message to synthesize.", "warning")
                await bus.publish("notification")
                return None
            sources.append(node)

        branches = [
            (f"Branch {index + 1}", document.chat_branch_history(node.id))
            for index, node in enumerate(sources)
        ]
        formatted = _format_branches_for_comparison(branches)

        parent = sources[0]
        avg_x = sum(node.x for node in sources) / len(sources)
        max_y = max(node.y for node in sources)
        route = composer_document.route()

        result_holder: dict[str, str] = {}

        async def _on_success(text):
            if any(node_id not in document.nodes for node_id in ids):
                # A source was deleted mid-flight - same liveness posture as
                # compare_branches's own on_success guard.
                return
            node = document.add_chat_node(
                avg_x, max_y + MESSAGE_VERTICAL_SPACING, text, False, parent_id=parent.id,
            )
            document.mark_branch_synthesis(
                node.id, ids, clean_instructions, route.get("provider"), route.get("modelLabel"),
            )
            document.last_chat_node_id = node.id
            result_holder["node_id"] = node.id
            await bus.publish("scene")

        def _on_failure(message):
            # start_branch_synthesis already surfaced the notification.
            pass

        await agent_dispatcher.start_branch_synthesis(
            bus=bus,
            notifications_state=notifications,
            source_text=formatted,
            instructions=clean_instructions,
            on_success=_on_success,
            on_failure=_on_failure,
        )
        return result_holder.get("node_id")

    # R8a: the two note agents restored from the deleted Qt app - see
    # graphlink_note_agent.py's own docstring for why they were dead stubs.
    bus.register_intent("scene", "generateKeyTakeaway", generate_key_takeaway)
    bus.register_intent("scene", "generateExplainerNote", generate_explainer_note)
    bus.register_intent("scene", "compareBranches", compare_branches)
    bus.register_intent("scene", "synthesizeBranches", synthesize_branches)
