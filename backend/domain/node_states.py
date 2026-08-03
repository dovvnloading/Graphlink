"""ADR-002 stage 2.5 (backend-only): typed per-kind SceneNode state.

SceneNode used to carry all ~95 fields for every one of its 16 kinds
regardless of which kind actually used them. This module holds one
dataclass per kind - only the fields that kind actually uses - attached to
SceneNode.state. Migration proceeds kind-by-kind (see each state class's
own relocation note); a kind not yet migrated still keeps its fields
directly on SceneNode.

Wire-compatibility constraint: SceneDocument.scene_payload() must keep
emitting the exact same flat per-node shape it does today. This module
introduces no wire-layer change by itself - it only relocates where a
field lives in memory; scene_payload's own per-kind read expressions are
updated in lockstep with each kind's migration, in backend/domain/graph.py.

kind values with no state class (no kind-specific fields at all, so
node.state stays None for them permanently): placeholder, thinking,
conversation.
"""

from __future__ import annotations

from dataclasses import dataclass


class NodeState:
    """Marker base for all per-kind node state payloads."""


@dataclass
class ImageState(NodeState):
    """Relocated verbatim from SceneNode.image_asset_id (former
    backend/domain/model.py field, R3.21) - the opaque reference key into
    SceneDocument.image_assets. See that dict's own docstring for the
    transport-decision reasoning (image bytes never live on the node
    itself)."""

    image_asset_id: str = ""


@dataclass
class HtmlState(NodeState):
    """Relocated verbatim from SceneNode.html_splitter_state (former
    backend/domain/model.py field, R6.3) - an HtmlViewNode's persisted
    draggable code/preview splitter position. None means "use the
    frontend's own default", not "0" - a real 0.0 position (fully
    collapsed to one side) must round-trip distinctly from "never set"."""

    html_splitter_state: float | None = None


@dataclass
class ArtifactState(NodeState):
    """Relocated verbatim from SceneNode.artifact_content (former
    backend/domain/model.py field, R5.2) - the Artifact/Drafter node's
    whole-document text. The model returns the WHOLE document every turn
    (never a diff/patch - see complete_artifact_generation), so this
    field is bounded by the model's own per-turn output ceiling, not by
    session length. The turn-by-turn conversation reuses SceneNode's own
    generic `history` list field (already used by ConversationNode)
    rather than a second list-typed field here - only this one scalar is
    needed."""

    artifact_content: str = ""


@dataclass
class CodeState(NodeState):
    """Relocated verbatim from SceneNode.code/SceneNode.language (former
    backend/domain/model.py fields, R3.5) - a code-block node's raw text
    and its declared language label (used for both the title's language
    prefix and the frontend's syntax-highlighting choice)."""

    code: str = ""
    language: str = ""


@dataclass
class NoteState(NodeState):
    """Relocated verbatim from SceneNode.is_system_prompt/is_summary_note/
    is_branch_comparison (former backend/domain/model.py fields) - a
    note's three mutually-independent badge flags. is_system_prompt/
    is_summary_note are the legacy system-prompt/summary-note badges;
    is_branch_comparison (ADR-002 Workstream 1, "Compare Branches") marks
    a note as the output of the Compare Branches agent - deliberately a
    separate flag from is_summary_note rather than reusing it, since that
    flag is legacy's own unrelated "Group Summary" concept."""

    is_system_prompt: bool = False
    is_summary_note: bool = False
    is_branch_comparison: bool = False
