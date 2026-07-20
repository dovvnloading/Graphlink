"""The document-viewer island's outbound wire contract, as a typed Python
dataclass.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. Unlike HelpStatePayload, this island IS
content-carrying: `content` holds the markdown text produced by
graphlink_window.py's _extract_document_view_content() ladder, unchanged from
today's isinstance-branch output - only the rendering target moves (from a
QTextEdit.setHtml() call to react-markdown), not the content itself. Cross-
checked against a live DocumentViewerBridge snapshot by
tests/test_document_viewer_payload_schema.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DocumentViewerStatePayload:
    """The complete published snapshot: the envelope fields IslandBridge.
    publish() adds to every island's payload, plus this island's one content
    field."""

    schemaVersion: int
    revision: int
    content: str
    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
