"""The composer island's outbound wire contract, as typed Python dataclasses.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - which is why every field name here
is camelCase rather than Python's usual snake_case. These classes exist for
exactly one purpose: to describe, field-for-field, the JSON that
ComposerBridge._build_state_payload() actually emits and that
web_ui/src/islands/composer/bridgeTypes.ts currently mirrors by hand. Naming
them identically to the wire keys means there is no translation layer to drift
- the mapping is the identity function, verifiable by eye. A snake_case
version with a rename step would be more idiomatic Python and strictly worse
here, since the rename table would become a second place for the contract to
go wrong.

Deliberately NOT reusing graphlink_composer.py's ComposerAttachment /
ComposerDraft: those are ComposerController's internal domain models and they
genuinely differ from the wire shape (attachment_id vs id, token_estimate vs
tokenCount, preparation_state vs preparationState, plus domain-only fields like
`path` that the wire firewall exists specifically to keep OUT of the payload).
Conflating them would either leak `path` to the web side - undoing the
id-not-path firewall - or require the domain models to carry wire concerns.

Nothing in the running app constructs these yet; they are the schema source of
truth, cross-checked against the live payload by
tests/test_composer_payload_schema.py, which validates a REAL ComposerBridge
snapshot against these definitions. That test is what makes these classes
authoritative rather than aspirational.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Mirrors bridge.ts's RequestState union exactly.
RequestState = Literal[
    "idle",
    "preparing",
    "uploading",
    "waiting",
    "generating",
    "finalizing",
    "canceled",
    "failed",
    "succeeded",
]

RouteMode = Literal["cloud", "ollama", "llamacpp", "unknown"]

# The only two values anything in the Python codebase ever assigns to
# ComposerDraft.send_mode (graphlink_composer.py:73,240) - matches what the
# hand-written TS type declared before this file replaced it. contextMode is
# genuinely NOT narrowed the same way: its only observed value is "branch",
# but set_branch()'s own signature accepts an arbitrary str with no
# enumeration anywhere, so typing it as a closed set here would assert a
# constraint the producer doesn't actually enforce.
SendMode = Literal["enter_to_send", "ctrl_enter_to_send"]


@dataclass
class ComposerDraftPayload:
    id: str
    text: str
    contextMode: str
    sendMode: SendMode
    restored: bool


@dataclass
class ComposerContextAnchorPayload:
    id: str
    label: str
    type: str


@dataclass
class ComposerAttachmentPayload:
    id: str
    name: str
    kind: str
    tokenCount: int
    preparationState: str
    contextLabel: str


@dataclass
class ComposerContextPayload:
    anchor: ComposerContextAnchorPayload | None
    items: list[ComposerAttachmentPayload]
    totalTokens: int
    reviewAvailable: bool


@dataclass
class ComposerModelOptionPayload:
    id: str
    label: str
    provider: str
    source: str
    active: bool
    ready: bool
    available: bool
    capabilities: list[str]


@dataclass
class ComposerReasoningOptionPayload:
    id: str
    label: str
    description: str


@dataclass
class ComposerReasoningPayload:
    level: str
    label: str
    options: list[ComposerReasoningOptionPayload]


@dataclass
class ComposerRoutePayload:
    mode: RouteMode
    provider: str
    modelId: str
    modelLabel: str
    modelOptions: list[ComposerModelOptionPayload]
    reasoning: ComposerReasoningPayload
    label: str
    available: bool
    canChange: bool
    # Only the llama.cpp branch emits this (the on-disk model path, as opposed
    # to modelId's basename), so it is genuinely absent from other routes -
    # optional rather than empty-string-defaulted, matching the real payload.
    modelValue: str | None = None


@dataclass
class ComposerRequestPayload:
    id: str | None
    state: RequestState
    message: str
    canSend: bool
    canCancel: bool
    canRetry: bool


@dataclass
class ComposerCapabilitiesPayload:
    attachments: bool
    contextReview: bool
    routeSelection: bool
    modelSelection: bool
    reasoningSelection: bool
    settingsShortcut: bool
    cancellation: bool


@dataclass
class ComposerThemePalettePayload:
    userNode: str
    aiNode: str
    selection: str
    navHighlight: str


@dataclass
class ComposerThemeSemanticPayload:
    searchHighlight: str
    statusInfo: str
    statusSuccess: str
    statusError: str
    statusWarning: str
    artifact: str
    conversationUserBubble: str
    conversationAiBubble: str
    default: str


@dataclass
class ComposerThemeNeutralButtonPayload:
    background: str
    hover: str
    pressed: str
    border: str
    icon: str
    mutedIcon: str


@dataclass
class ComposerThemeGraphNodePayload:
    border: str
    header: str
    dot: str
    hoverDot: str
    hoverOutline: str
    selectedOutline: str
    bodyStart: str
    bodyEnd: str
    headerStart: str
    headerEnd: str
    badgeFill: str
    panelFill: str
    panelBorder: str


@dataclass
class ComposerThemePayload:
    mode: str
    name: str
    cssVariables: dict[str, str]
    palette: ComposerThemePalettePayload
    semantic: ComposerThemeSemanticPayload
    neutralButton: ComposerThemeNeutralButtonPayload
    graphNode: ComposerThemeGraphNodePayload


@dataclass
class ComposerStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    draft: ComposerDraftPayload
    context: ComposerContextPayload
    route: ComposerRoutePayload
    request: ComposerRequestPayload
    capabilities: ComposerCapabilitiesPayload
    theme: ComposerThemePayload
    # The oldest reader version this payload still works with. Lets the sender
    # signal a BREAKING change explicitly, instead of the reader inferring
    # compatibility from the version number alone. See IslandBridge's constants
    # for the full negotiation rules.
    #
    # OPTIONAL on purpose, and this must stay consistent with
    # lib/bridge-core/schemaVersion.ts, which treats an absent value as "the
    # sender declared no floor" rather than as an error: a sender predating
    # this field is otherwise perfectly readable, so requiring it here would
    # hard-fail a payload the negotiation logic itself considers fine. Every
    # current sender does emit it (IslandBridge.publish adds it
    # unconditionally); optionality models older senders, not today's. Last in
    # the field order because a defaulted dataclass field must follow the
    # non-defaulted ones - the wire is a JSON object, so field order carries
    # no meaning on it.
    minCompatibleSchemaVersion: int | None = None
