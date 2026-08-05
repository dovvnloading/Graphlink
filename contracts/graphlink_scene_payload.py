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

R5.2 adds `artifactContent` (the Artifact/Drafter node's real persisted
shape): the model returns the WHOLE document every turn (whole-document
replace, never a diff/patch), so this single scalar always holds the latest
full document text. Populated for kind=="artifact" rows, defaulted (empty
string) for every other kind, same additive rule. The turn-by-turn
conversation reuses the existing `history` field (R3.25) rather than a new
list-typed field.

R5.3 adds the Gitlink node's 16 `gitlink*` fields: populated for
kind=="gitlink" rows, defaulted (empty string/list/dict/None) for every
other kind, same additive rule. `gitlinkContextXml` is DELIBERATELY NOT
one of these 16 - repository.py's build_context_bundle can produce up to
180,000 chars of XML (MAX_CONTEXT_CHARS), an order of magnitude above every
other Gitlink field's implicit ceiling, and scene_payload() resends every
node on ~20 undebounced triggers - inlining that blob here would reproduce
the exact cost the R3.21 image_assets transport decision (see that field's
own comment) was designed to avoid. It is served on demand via the
read-only fetchGitlinkContext intent instead, never as part of this
snapshot. `gitlinkPendingChanges` is a list of `GitlinkPendingChangeRow` (a
proper nested dataclass, matching the convention `ResearchSourceRow`
already established for a list-of-structured-object field, rather than a
loose dict) - `content` is `str | None` (not required) because a `delete`
operation's normalized change item genuinely omits the `content` key
entirely (see GitlinkAgent._normalize_files), and the schema generator's
required/optional split is driven purely by `X | None` typing, not by a
dataclass default value.

R5.3 post-review FIX 6 adds `gitlinkContextVersion` (a genuine monotonic
per-node counter, see the field's own comment on SceneNodeRow below) -
UNLIKE `gitlinkContextXml`/the backend-only `gitlink_change_local_root`,
this one DOES belong on the wire: the frontend's Context-tabs lazy-fetch
guard needs it to detect a new Build Context result even when
`gitlinkContextSummary` happens to repeat.
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
    # codegen's schema generator (payload_schema.py) has a closed,
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
class GitlinkPendingChangeRow:
    path: str
    operation: str
    reason: str
    # str | None (not just a defaulted str): a `delete` operation's
    # normalized change item genuinely omits the `content` key entirely (see
    # GitlinkAgent._normalize_files) - the schema generator's required/
    # optional split is driven by `X | None` typing, not by a dataclass
    # default value, so this must be Optional for a delete-only item to
    # validate.
    content: str | None = None


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
    # R5.2: the Artifact/Drafter node's real persisted shape - the latest
    # full document text (whole-document replace every turn, never a
    # diff/patch). Populated for kind=="artifact" rows, defaulted (empty
    # string) for every other kind.
    artifactContent: str = ""
    # R5.3: the Gitlink node's real persisted shape - populated for
    # kind=="gitlink" rows, defaulted for every other kind. gitlinkContextXml
    # is DELIBERATELY NOT one of these fields - see this module's own
    # docstring for why (served on demand via fetchGitlinkContext instead).
    gitlinkRepo: str = ""
    gitlinkBranch: str = ""
    gitlinkScopeMode: str = "selected"
    gitlinkLocalRoot: str = ""
    gitlinkRepoFilePaths: list[str] = field(default_factory=list)
    gitlinkSelectedPaths: list[str] = field(default_factory=list)
    gitlinkTaskPrompt: str = ""
    # dict[str, str], not the mixed int/str shape repository.py's
    # build_context_bundle actually returns - backend/canvas.py's
    # store_gitlink_context stringifies every value before this ever reaches
    # the wire (see that method's own comment), so this stays honestly
    # dict[str, str] end to end.
    gitlinkContextStats: dict[str, str] = field(default_factory=dict)
    gitlinkContextSummary: str = ""
    # R5.3 post-review FIX 6: a genuine monotonic per-node counter,
    # incremented unconditionally every time backend/canvas.py's
    # store_gitlink_context lands a successful Build Context result - unlike
    # gitlinkContextSummary (built purely from aggregate file counts, never
    # from paths/content), two different Build Context results can never
    # collide here. Closes a real bug: the frontend's Context-tabs
    # lazy-fetch-once guard used to key on gitlinkContextSummary alone, and
    # two DIFFERENT builds (e.g. selecting a different single file each
    # time) could produce an IDENTICAL summary string, so the guard
    # incorrectly skipped refetching and showed stale XML. See
    # backend/domain/node_states.py's GitlinkState.gitlink_context_version
    # for the full rationale.
    gitlinkContextVersion: int = 0
    gitlinkProposalMarkdown: str = ""
    gitlinkPendingChanges: list[GitlinkPendingChangeRow] = field(default_factory=list)
    gitlinkPreviewText: str = ""
    gitlinkChangeFingerprint: str | None = None
    gitlinkChangeState: str = "draft"
    gitlinkError: str = ""
    # R5.4: the Py-Coder node's real persisted shape - populated for
    # kind=="pycoder" rows, defaulted for every other kind.
    pycoderMode: str = "ai_driven"
    pycoderPrompt: str = ""
    pycoderCode: str = ""
    pycoderOutput: str = ""
    pycoderAnalysis: str = ""
    pycoderLastRunFailed: bool = False
    pycoderAwaitingApproval: bool = False
    pycoderError: str = ""
    # R5.4: the Execution Sandbox node's real persisted shape - populated for
    # kind=="code_sandbox" rows, defaulted for every other kind.
    # codeSandboxSandboxId is DELIBERATELY NOT one of these fields - see
    # backend/domain/node_states.py's own comment on
    # CodeSandboxState.code_sandbox_sandbox_id (pure internal
    # directory-naming key, mirrors gitlink_imported_root's own
    # "server-side bookkeeping only" precedent).
    codeSandboxRequirements: str = ""
    codeSandboxPrompt: str = ""
    codeSandboxCode: str = ""
    codeSandboxOutput: str = ""
    codeSandboxAnalysis: str = ""
    codeSandboxAwaitingApproval: bool = False
    # R5.4 CODESANDBOX FIX (closing the requirements-disclosure staleness
    # race): a display-only snapshot of the EXACT requirements manifest this
    # specific pending approval refers to, frozen the instant
    # codeSandboxAwaitingApproval flips True - deliberately distinct from
    # codeSandboxRequirements above (the user's still-live, still-editable
    # draft for the NEXT run). See backend/domain/node_states.py's own
    # comment on CodeSandboxState.code_sandbox_approval_requirements for the
    # full race this closes.
    codeSandboxApprovalRequirements: str = ""
    # ADR-005 stage 5.5: the user's live source-build opt-in for the CURRENT
    # pending approval, reset to False every time a new gate opens - see
    # backend/domain/node_states.py's own comment on CodeSandboxState.
    # code_sandbox_approval_allow_source_builds for the full race this
    # closes.
    codeSandboxApprovalAllowSourceBuilds: bool = False
    # ADR-005 stage 5.5 review-fix: True only while the CURRENT pending
    # approval is a repair-loop re-gate, not the initial gate - see
    # backend/domain/node_states.py's own comment on CodeSandboxState.
    # code_sandbox_approval_is_repair for why the frontend needs this.
    codeSandboxApprovalIsRepair: bool = False
    codeSandboxError: str = ""


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
    # R7.5b-1: Qt-removal plan R7.5's first canvas-visual parity fix -
    # populated 1:1 from backend/canvas.py's SceneDocument.fade_connections_enabled.
    fadeConnectionsEnabled: bool
    # R7.5b-2: Qt-removal plan R7.5's second canvas-visual parity fix -
    # populated 1:1 from backend/canvas.py's SceneDocument.orthogonal_routing.
    orthogonalRouting: bool
    # R7.5b-3: the third and final canvas-visual parity fix - populated 1:1
    # from backend/canvas.py's SceneDocument.smart_guides.
    smartGuides: bool
    # R7.5c: True when this scene corresponds to a saved chats.db row -
    # derived from backend/canvas.py's SceneDocument.current_chat_id, which
    # itself stays server-side. Lets the frontend evaluate legacy's New Chat
    # confirm-skip predicate ("empty canvas AND no current chat").
    hasSavedChat: bool
    dragFactor: float
    fontFamily: str
    fontSizePt: int
    fontColor: str
    minCompatibleSchemaVersion: int | None = None
