"""The scene topic's wire contract (Qt-removal plan R1) - the canvas
document the React Flow canvas renders: nodes, edges, navigation pins, and
canvas settings, published as full snapshots over the WS event bus.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL (the graphlink_composer_payload.py
convention): the domain lives in backend/canvas.py's SceneDocument, which
sources GridViewSettings and NavigationPinStore - this module only fixes the
JSON shape and generates the TS type + runtime validator the SPA consumes.

R1 nodes are placeholders (`kind: "placeholder"`); R3 extends `kind` per
migrated node type - additive only, per the schema-versioning contract.

R3.1 adds the `chat` kind's fields (content/isUser/isCollapsed - the real
persisted shape from the legacy ChatNode's serializer, minus everything
Qt-only): populated for kind=="chat" rows, defaulted (empty/false) for every
other kind, so the schema stays additive-only as new kinds land.

R3.5 adds the `code` kind's fields (code/language): populated for kind=="code"
rows, defaulted (empty string) for every other kind, same additive rule.

R3.9 adds the `document` kind's fields (attachmentKind/filePath/mimeType/
durationSeconds/byteSize/previewLabel - the DocumentNode attachment
metadata): populated for kind=="document" rows, defaulted (empty
string/None) for every other kind, same additive rule.

R3.13 adds `isDocked` (the ThinkingNode/docked-child increment): true when a
node is currently docked into its parent's docked-child slot, populated for
any kind that has been docked, defaulted false otherwise - same additive
rule.

R3.21 adds `imageAssetId` (the ImageNode increment): the opaque asset-store
key an image-kind node's bytes live under (fetched separately over HTTP,
never inlined here), populated for kind=="image" rows, defaulted (empty
string) for every other kind, same additive rule.

R3.25 adds `history` (the ConversationNode increment): a growing list of
role/content messages - the one R3 kind whose own field is a LIST rather
than a scalar. Populated for kind=="conversation" rows, defaulted (empty
list) for every other kind, same additive rule.

R4.3 adds `pendingRequestId` (ConversationNode real-reply + per-node cancel):
the id of the AgentDispatcher request currently generating a reply for a
node, or None when idle. Generic across any kind that ever gets its own real
dispatch slot, defaulted None for every other kind, same additive rule.

R5.1 adds the Web Research node's six `research*` fields (query text reuses
the existing `content` field, same as code/thinking/html): populated for
kind=="web_research" rows, defaulted (empty string/0/None) for every other
kind, same additive rule. `researchResult`, when present, is a nested
ResearchResultRow - the one field here (besides R3.25's `history`) whose
shape is a structured object rather than a scalar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ConversationMessageRow:
    role: Literal["user", "assistant"]
    content: str


@dataclass
class ResearchSourceRow:
    sourceId: str
    title: str
    url: str
    canonicalUrl: str
    snippet: str
    rank: int
    provider: str
    finalUrl: str
    status: str
    errorCode: str
    errorMessage: str
    truncated: bool
    contentHash: str
    citationCount: int


@dataclass
class ResearchCitationRow:
    sourceId: str
    marker: str
    claimContext: str


@dataclass
class ResearchResultRow:
    requestId: str
    originalQuery: str
    effectiveQuery: str
    answerMarkdown: str
    sources: list[ResearchSourceRow]
    citations: list[ResearchCitationRow]
    warnings: list[str]
    # DEVIATION from the R5.1 spec text (which said a bare `dict`): the
    # codegen's schema generator (graphlink_island_schema.py) has a closed,
    # deliberately-narrow supported-type set that does NOT include a bare
    # `dict` or `dict[str, Any]` (there is no catch-all/`Any` case - see that
    # module's own docstring) - only `dict[str, X]` for X itself in the
    # supported set. provider_snapshot is genuinely free-form diagnostics at
    # the domain layer (graphlink_plugins/web_research/domain.py's
    # WebResearchRequest/ResearchResult.provider_snapshot: dict[str, Any]),
    # but nothing in this increment ever populates it (agents.py's
    # WebResearchRequest(...) call site never passes provider_snapshot, so it
    # is always {} at runtime here) - dict[str, str] is the narrowest
    # accurate supertype of "empty dict" that the generator supports, so it
    # is used here rather than blocking codegen. A future increment that
    # starts populating this with non-string values needs its own explicit
    # schema-generator extension, not a silent workaround here.
    providerSnapshot: dict[str, str]


@dataclass
class SceneNodeRow:
    id: str
    x: float
    y: float
    title: str
    kind: str
    content: str = ""
    isUser: bool = False
    isCollapsed: bool = False
    # R3.5: the code node's real persisted shape - populated for kind=="code"
    # rows, defaulted (empty string) for every other kind.
    code: str = ""
    language: str = ""
    # R3.9: the document node's real persisted shape (attachment metadata) -
    # populated for kind=="document" rows, defaulted (empty string/None) for
    # every other kind.
    attachmentKind: str = ""
    filePath: str = ""
    mimeType: str = ""
    durationSeconds: float | None = None
    byteSize: int | None = None
    previewLabel: str = ""
    # R3.13: the ThinkingNode/docked-child increment - populated for any node
    # that has been docked into its parent's docked-child slot, defaulted
    # false for every other node.
    isDocked: bool = False
    # R3.21: the image node's opaque reference key into the asset store
    # (backend/assets.py's GET /api/assets/{id}) - populated for kind=="image"
    # rows, defaulted (empty string) for every other kind. The image bytes
    # themselves never appear in this payload - see the transport-decision
    # comment on backend/canvas.py's SceneDocument.image_assets.
    imageAssetId: str = ""
    # R3.25: the ConversationNode's real persisted shape - a growing list of
    # role/content messages, populated for kind=="conversation" rows,
    # defaulted (empty list) for every other kind. The one R3 kind whose own
    # field is a list rather than a scalar.
    history: list[ConversationMessageRow] = field(default_factory=list)
    # R4.3: transient per-node in-flight-request marker - the id of the
    # AgentDispatcher request currently generating a reply for this node, or
    # None when idle. Generic across any kind that ever gets its own real
    # dispatch slot (not conversation-only), defaulted None for every other
    # kind.
    pendingRequestId: str | None = None
    # R5.1: the Web Research node's real persisted shape - query text reuses
    # `content` above (same pattern as code/thinking/html); these six fields
    # track one research run's live progress/outcome, populated for
    # kind=="web_research" rows, defaulted (empty string/0/None) for every
    # other kind.
    researchStage: str = ""
    researchCompleted: int = 0
    researchTotal: int = 0
    researchActiveSourceId: str | None = None
    researchError: str = ""
    researchResult: ResearchResultRow | None = None


@dataclass
class SceneEdgeRow:
    id: str
    source: str
    target: str


@dataclass
class ScenePinRow:
    id: str
    title: str
    note: str
    x: float
    y: float


@dataclass
class SceneStatePayload:
    """The complete published snapshot, including the envelope fields the
    event bus stamps onto every topic's payload."""

    schemaVersion: int
    revision: int
    nodes: list[SceneNodeRow]
    edges: list[SceneEdgeRow]
    pins: list[ScenePinRow]
    snapToGrid: bool
    dragFactor: float
    fontFamily: str
    fontSizePt: int
    fontColor: str
    minCompatibleSchemaVersion: int | None = None
