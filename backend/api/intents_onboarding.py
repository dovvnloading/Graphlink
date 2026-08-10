"""ADR-012 stage 12.6: the bundled sample workspace.

One intent, one fixed fixture: `loadSampleWorkspace` populates the CURRENT
session's scene with a small, hardcoded 3-node demo (a note explaining what
Graphlink is, plus a short chat exchange) - no LLM call, nothing random, so
it is trivially repeatable both for a new user's first click and for an E2E
test fixture (doc/adr/ADR-012-ui-ux-system.md's stage 12.6 exit criterion:
"E2E uses the sample fixture"). See OnboardingDialog.tsx (web_ui) for the
frontend wizard that calls this, and SceneCanvas.tsx's empty-canvas hint for
the other caller.

Registered the same "scene" topic every other canvas-mutating intent uses
(register_node_intents/register_groups_intents precedent) rather than a new
topic - this creates ordinary scene nodes through the ordinary SceneDocument
API, it just picks their content for the caller instead of the caller typing
it.
"""

from __future__ import annotations

from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.events import SessionBus

# Fixed, hand-written content - deliberately NOT generated at request time.
# Keeping this hardcoded (rather than, say, a one-shot LLM call) is what
# makes the fixture deterministic: the same 3 nodes, same kinds, same text,
# every single call, on a fresh machine with no provider configured at all.
SAMPLE_NOTE_CONTENT = (
    "Welcome to Graphlink - a visual AI workspace. Every message becomes a "
    "node on this canvas; branch a conversation by replying from any earlier "
    "node, and connect nodes into a graph as your thinking grows. The short "
    "exchange below shows the idea in miniature."
)
SAMPLE_USER_MESSAGE = "What can I do with Graphlink?"
SAMPLE_ASSISTANT_MESSAGE = (
    "Graphlink turns each conversation turn into a node you can branch, "
    "compare, and connect - explore multiple directions from the same "
    "prompt, write and run code inline, or bring in documents and web "
    "research alongside your chat. Try replying to any node to start your "
    "own branch, or just type a message below to begin for real."
)


def register_onboarding_intents(bus: SessionBus, document: SceneDocument) -> None:
    publish_scene = make_publish_scene(bus)

    async def load_sample_workspace():
        # ADR-010 stage 10.3: one composite so a single Ctrl+Z removes the
        # whole sample workspace as one action, not 3 separate undo steps for
        # a fixture the user did not build node-by-node themselves - the
        # documented use case composite() itself names (commands.py's own
        # docstring).
        with document.composite("loadSampleWorkspace", "user"):
            note, _command = document.record_command(
                "addNote", "user", lambda: document.add_note(-360, -140),
            )
            document.record_command(
                "setNoteContent", "user",
                lambda: document.set_note_content(note.id, SAMPLE_NOTE_CONTENT),
                node_ids=[note.id],
            )
            user_node, _command = document.record_command(
                "addChatNode", "user",
                lambda: document.add_chat_node(40, -140, SAMPLE_USER_MESSAGE, True),
            )
            document.record_command(
                "addChatNode", "user",
                lambda: document.add_chat_node(
                    40, 60, SAMPLE_ASSISTANT_MESSAGE, False, user_node.id,
                ),
                node_ids=[user_node.id],
            )
        await publish_scene()

    bus.register_intent("scene", "loadSampleWorkspace", load_sample_workspace)
