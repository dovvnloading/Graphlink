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

ADR-003 stage 3.3 (C9) adds 26 fields backend/domain/graph.py's
scene_payload() was ALREADY emitting on the real wire - unlike every
field above, these were never declared here at all. The frontend read
them through 8 unsafe `n as SceneNodeRow & LocalInterface` casts in
SceneCanvas.tsx instead (TypeScript would not otherwise let it access a
field the generated type didn't have), which is exactly what ADR-002's
audit finding C9 ("~26 wire fields bypass the contract behind 8 `as`
casts") names. This stage closes the gap the OTHER direction: rather
than stop emitting these fields (they are real, shipped, load-bearing
UI state - branch lifecycle badges, synthesis provenance, note/frame/
container styling and sizing, real chart nodes, the R6.3 splitter/
scroll-position round-trip), it declares them here so codegen emits a
real type and a real runtime validator for them, and the unsafe casts
can be deleted outright rather than merely narrowed.

`chartType`/`chartData`/`chartError`/`chartWidth`/`chartHeight`/
`chartAspectLocked`/`chartSourceNodeId` are the R6.2 chart node's real
persisted shape - populated for kind=="chart" rows, defaulted for every
other kind, same additive rule as every field above. `chartAssetId`/
`chartAssetVersion` (the backend-rendered display PNG's own key/version)
rode this same shape until ADR-013 stage 13.4 retired them - the client-
side interactive renderer (stage 13.2) draws straight from chartData, so
nothing had read them since. `chartData` is the one genuinely hard case: graphlink_chart_data.
py's canonicalize_chart_data() returns one of THREE different shapes
depending on chart type (bar/line/pie: labels+values+xAxis+yAxis;
histogram: values+bins+xAxis+yAxis; sankey: flows only) - a real
discriminated union the schema generator's closed type set has no direct
way to express (no tagged-union support). ChartDataRow below resolves
this the same way SceneNodeRow ITSELF already resolves the analogous
problem one level up (one node kind's fields, present; every other
kind's fields, absent-and-defaulted): every field across all three chart
shapes is declared, all of them optional, so a real chart's dict
(always a subset of these) and a non-chart node's `{}` (all absent) both
validate cleanly - not a new pattern, the same one this whole file
already uses at the top level, applied one level deeper.

`color`/`headerColor`/`isSystemPrompt`/`isSummaryNote`/`isBranchComparison`/
`itemIds`/`isLocked`/`groupWidth`/`groupHeight` are the R6.1 Notes/Frames/
Containers fields - `color`/`headerColor`/`itemIds` are genuinely generic
(every node kind carries SceneNode.color/header_color/item_ids as core
fields, populated whenever the frontend/backend chooses to use them for
that kind - not gated behind an isinstance(state, ...) check the way the
kind-specific fields below are), the rest populated for kind in
("note", "frame", "container") and defaulted for every other kind.
`groupManualWidth`/`groupManualHeight` are DELIBERATELY NOT among these -
same "server-side bookkeeping only" posture as `gitlinkImportedRoot`/
`codeSandboxSandboxId` above.

`provider`/`model`/`isBranchSynthesis`/`synthesisInstructions`/
`branchStatus`/`isFinalDeliverable` are ADR-002 Workstream 1's branch-
lifecycle and Synthesize-Branches fields - populated for kind=="chat"
rows (branchStatus/isFinalDeliverable read for every kind, since a
non-chat node can still be marked the branch's final deliverable), same
additive rule.

`htmlSplitterState`/`chatScrollValue` are the R6.3 UI-position round-trip
fields (Source/Preview splitter position, chat scroll position) -
populated for kind=="html"/kind=="chat" respectively, defaulted for
every other kind. `contentParts` is DELIBERATELY NOT one of these 26:
it is a real wire field (backend/domain/graph.py's _content_parts_wire)
but SceneCanvas.tsx never reads it today (a backend-only multimodal
round-trip capability, not a rendered feature) - C9 is specifically
about fields an unsafe CAST reaches for, and no cast reaches for this
one, so adding it here would not serve this stage's own exit criterion.

ADR-007 stage 7.4 adds `toolCalls` (a list of `ToolInvocationRow`): the
tool calls + results an assistant chat-node's turn made, populated for
kind=="chat" rows whose turn actually invoked tools, defaulted `[]` for
every other kind and for tool-less chat turns - same additive rule as
everything above. See ToolInvocationRow's own docstring for why its
`arguments` field is a JSON-encoded string, not a nested object.

ADR-014 stage 14.2 adds `pluginState` (dict[str, str]): the Plugin SDK's
generic live-wire fallback for a THIRD-PARTY plugin's own NodeState
subclass fields - populated only for a plugin-registered node kind whose
author opted into HostContext.register_node_kind(..., serialize=...)
(backend/plugin_sdk.py), defaulted `{}` for every built-in kind and for
any plugin kind that never opted in, same additive rule as everything
above. dict[str, str], not a richer nested shape, for the SAME reason
ResearchResultRow.providerSnapshot above is dict[str, str] rather than a
bare dict - graphlink_wire_schema.py's generator has no `Any`/free-form-
object construct, and a third-party plugin's own state shape is
definitionally unknowable to this schema ahead of time. See backend/
domain/graph.py's SceneDocument.plugin_node_serializers/_plugin_state_wire
for the populating side. Not read by the frontend today (same "on the
wire, not yet a rendered feature" posture `contentParts` above already
established) - real dynamic frontend plugin rendering is stage 14.5's job,
per ADR-014's own stage 14.1 scoping decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ConversationMessageRow:
    role: Literal["user", "assistant"]
    content: str
    # ADR-006 stage 6.4 (H5): True marks a PARTIAL assistant reply whose
    # stream died mid-generation - the accumulated text is preserved and
    # rendered with an interrupted marker instead of being lost.
    incomplete: bool = False


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
    # codegen's schema generator (graphlink_wire_schema.py) has a closed,
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
class ToolInvocationRow:
    """ADR-007 stage 7.4: one tool call + its result, attached to the
    chat-kind assistant node whose turn made it - see SceneNodeRow.toolCalls'
    own comment. `argumentsJson` is the call's arguments JSON-ENCODED as a
    string, not a nested object: ToolCall.arguments (backend/providers/
    base.py) is an arbitrary caller-defined JSON object shaped by whatever
    tool was called, and graphlink_wire_schema.py's generator has no
    construct for "any JSON object" (its own module docstring lists the
    closed type set this deliberately refuses to guess beyond) - the same
    reasoning that already makes gitlinkContextXml/similar opaque blobs
    cross the wire as strings rather than structured fields. The frontend
    renders it as formatted JSON text, which needs no structural typing
    anyway."""

    id: str
    name: str
    argumentsJson: str
    result: str
    isError: bool = False


@dataclass
class PlanStepRow:
    """ADR-008 stage 8.3: one Builder plan step - the typed wire shape of
    PlanState.steps' own {"id","title","status","detail"} dicts (backend/
    domain/node_states.py). status is one of pending|running|done|failed|
    skipped - pinned as a plain string rather than an enum for the same
    additive-evolution reason researchStage crosses as a string."""

    id: str
    title: str
    status: str = "pending"
    detail: str = ""


@dataclass
class BuilderActivityRow:
    """ADR-008 stage 8.7: one row of a Builder run's activity log - the
    typed wire shape of PlanState.builder_activity's own {"tool","summary",
    "outcome","stepId","elapsedMs"} dicts (backend/domain/node_states.py).
    outcome is "ok"|"error" - pinned as a plain string for the same
    additive-evolution reason PlanStepRow's own status is."""

    tool: str
    summary: str
    outcome: str = "ok"
    stepId: str = ""
    elapsedMs: int = 0


@dataclass
class HarnessActivityRow:
    """PLAN-2026-08-24 H1: one row of a harness run's activity log - the
    typed wire shape of HarnessState.harness_activity's own {"tool",
    "summary","outcome","elapsedMs"} dicts (backend/domain/node_states.py).
    BuilderActivityRow minus stepId (a harness task has no checklist);
    outcome is "ok"|"error", pinned as a plain string for the same
    additive-evolution reason."""

    tool: str
    summary: str
    outcome: str = "ok"
    elapsedMs: int = 0


@dataclass
class ChartFlowRow:
    """One Sankey flow - the shape canonicalize_chart_data() (graphlink_
    chart_data.py) always builds every item of its "flows" list from, for
    chart_type=="sankey" specifically."""

    source: str
    target: str
    value: float


@dataclass
class ChartDataRow:
    """ADR-003 stage 3.3: canonicalize_chart_data() returns one of three
    disjoint shapes depending on chart type - see SceneNodeRow's own module
    docstring for why every field here is optional rather than this being
    three separate row types. `type`/`title` are set on every real chart's
    dict (never independently absent from each other in practice), but the
    schema generator has no dependent-required-fields concept, so both stay
    individually optional like every other field here - the same tolerance
    the file's existing `gitlinkChangeFingerprint`-style fields already
    accept."""

    # ADR-013 stage 13.1: the spec's own schema version - canonicalize_chart_data
    # always stamps 1 today. Bumped only when the CANONICAL SHAPE this dataclass
    # describes changes incompatibly (a new required field, a renamed key) - not
    # on every feature addition. A future migration reads this to know which
    # shape it's looking at rather than sniffing field presence.
    version: int | None = None
    type: Literal["bar", "line", "pie", "histogram", "sankey"] | None = None
    title: str | None = None
    # bar/line/pie
    labels: list[str] | None = None
    values: list[float] | None = None
    xAxis: str | None = None
    yAxis: str | None = None
    # histogram (values above is reused; xAxis/yAxis above too)
    bins: int | None = None
    # sankey
    flows: list[ChartFlowRow] | None = None


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
    # ADR-021 stage 21.5: the node's own "keep these sources in the local
    # knowledge store" opt-in. Populated for kind=="web_research" rows,
    # defaulted False for every other kind.
    researchRetainToKnowledge: bool = False
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
    # ADR-003 stage 3.3 (C9): ADR-002 Workstream 1's branch-lifecycle +
    # Synthesize-Branches fields - see this file's own module docstring.
    provider: str | None = None
    model: str | None = None
    isBranchSynthesis: bool = False
    synthesisInstructions: str = ""
    branchStatus: str = "active"
    # ADR-006 stage 6.4 (H5): chat-kind partial reply preserved after a dead
    # stream; the frontend offers Regenerate as the retry affordance.
    responseIncomplete: bool = False
    # ADR-006 stage 6.8: provider-reported token counts stamped on chat
    # replies (null for user messages, non-chat kinds, pre-6.8 nodes, and
    # providers that report nothing).
    promptTokens: int | None = None
    completionTokens: int | None = None
    # ADR-016 stage 16.2: the USD cost estimated when usage was stamped -
    # see backend/domain/node_states.py's ChatState.estimated_cost_usd.
    estimatedCostUsd: float | None = None
    isFinalDeliverable: bool = False
    # R6.1: Notes/Frames/Containers - color/headerColor/itemIds are generic
    # (SceneNode core fields, any kind), the rest populated for kind in
    # ("note", "frame", "container") and defaulted for every other kind.
    # groupManualWidth/groupManualHeight are DELIBERATELY NOT among these -
    # same "server-side bookkeeping only" posture as gitlinkImportedRoot/
    # codeSandboxSandboxId above.
    color: str | None = None
    headerColor: str | None = None
    isSystemPrompt: bool = False
    isSummaryNote: bool = False
    isBranchComparison: bool = False
    itemIds: list[str] = field(default_factory=list)
    isLocked: bool = True
    groupWidth: float | None = None
    groupHeight: float | None = None
    # R6.2: the chart node's real persisted shape - populated for
    # kind=="chart" rows, defaulted for every other kind. See this file's
    # own module docstring for why chartData is a ChartDataRow rather than
    # a scalar or a bare dict.
    chartType: str = ""
    chartData: ChartDataRow = field(default_factory=ChartDataRow)
    chartError: str = ""
    chartWidth: float = 680.0
    chartHeight: float = 500.0
    chartAspectLocked: bool = True
    chartSourceNodeId: str = ""
    # R6.3: the Source/Preview splitter position (html) and chat scroll
    # position (chat) round-trip fields - populated for kind=="html"/
    # kind=="chat" respectively, defaulted for every other kind.
    htmlSplitterState: float | None = None
    chatScrollValue: float = 0.0
    # ADR-007 stage 7.4: an assistant turn's tool calls + their results,
    # in call order - populated for kind=="chat" rows whose turn actually
    # invoked tools (the overwhelming majority of chat nodes have none, the
    # same "empty list, not null" default `history`/`itemIds` above already
    # use), defaulted [] for every other kind. Rendered as a collapsible
    # section (the ADR's own "an assistant turn that calls tools renders
    # the calls and their results (collapsible)") in ChatNodeView.tsx.
    toolCalls: list[ToolInvocationRow] = field(default_factory=list)
    # ADR-018 stage 18.3: an explicit model PIN - populated for kind=="chat"
    # rows that have one, "" (both fields together, never partial) for
    # every other row. The input-routing opposite of provider/model above
    # (which record what a completed reply WAS generated by); these decide
    # what the NEXT reply from this node - or, when this node is a branch
    # root, the branch - resolves to. See backend/domain/node_states.py's
    # own comment on ChatState.override_provider/override_model_id.
    overrideProvider: str = ""
    overrideModelId: str = ""
    # ADR-017 stage 17.5: branch-indexing opt-in - see backend/domain/
    # node_states.py's own comment on ChatState.index_into_knowledge.
    indexIntoKnowledge: bool = False
    # ADR-008 stage 8.3: the Builder plan node (kind=="plan") - the
    # checklist + run-state surface PlanNodeView renders. Populated only
    # for plan rows; every other kind carries the defaults. See
    # backend/domain/node_states.py's PlanState docstring for the
    # builder_status state machine and the plan-node-as-resume-point
    # contract these fields serialize.
    planGoal: str = ""
    planSteps: list["PlanStepRow"] = field(default_factory=list)
    builderActivity: list["BuilderActivityRow"] = field(default_factory=list)
    builderStatus: str = ""
    builderMode: str = ""
    builderRunId: str = ""
    builderMaxSteps: int = 0
    builderMaxTokens: int = 0
    builderMaxWallSeconds: int = 0
    builderSpentSteps: int = 0
    builderSpentTokens: int = 0
    builderSpentWallSeconds: int = 0
    builderAwaitingToolApproval: bool = False
    builderApprovalToolName: str = ""
    builderApprovalSummary: str = ""
    builderStatusDetail: str = ""
    # PLAN-2026-08-24 H1: the harness node's render surface. Populated only
    # for harness rows; conversation history deliberately never crosses the
    # wire (it lives in the node's workspace transcript - see
    # HarnessState's own docstring).
    harnessGoal: str = ""
    harnessReply: str = ""
    harnessStatus: str = ""
    harnessStatusDetail: str = ""
    harnessRunId: str = ""
    harnessActivity: list["HarnessActivityRow"] = field(default_factory=list)
    harnessContextTokens: int = 0
    harnessMaxContextTokens: int = 0
    harnessCompactions: int = 0
    harnessAwaitingApproval: bool = False
    harnessApprovalToolName: str = ""
    harnessApprovalSummary: str = ""
    harnessMaxTurns: int = 0
    harnessSpentTurns: int = 0
    harnessSpentTokens: int = 0
    # ADR-014 stage 14.2: the Plugin SDK's generic live-wire fallback for a
    # third-party plugin's own NodeState subclass fields - see this file's
    # own module docstring for the full rationale.
    pluginState: dict[str, str] = field(default_factory=dict)


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
    # ADR-010 stage 10.2: the undo/redo affordance's state. The LABELS are on
    # the wire, not just the booleans, because the button reads "Undo Delete"
    # and only the backend knows what is actually on top of the stack. Empty
    # string (not null) when there is nothing to undo/redo, so the frontend
    # never has to null-check before rendering - canUndo/canRedo is the
    # single source of truth for enablement.
    canUndo: bool = False
    canRedo: bool = False
    undoLabel: str = ""
    redoLabel: str = ""
    minCompatibleSchemaVersion: int | None = None
