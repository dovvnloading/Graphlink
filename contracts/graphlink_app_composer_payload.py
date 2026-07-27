"""The SPA composer topic's wire contract (Qt-removal plan R2.3).

A DISTINCT dataclass from graphlink_composer_payload.py's ComposerStatePayload
- same reasoning as graphlink_scene_payload.py's split from the Qt canvas
payloads in R1. The legacy island's ComposerStatePayload requires a `theme`
field (its QWebEngineView needs live-pushed :root tokens); the SPA is one
document that already loads real token CSS globally, so the field would be
pure duplication. Reusing the legacy dataclass would mean fabricating a
theme object nothing reads just to satisfy an old requirement - the wrong
kind of "wire compatibility." See backend/composer.py's own docstring for
the fuller architecture rationale.

The legacy graphlink_composer_payload.py is untouched and keeps serving the
Qt composer island until R7 deletes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RequestState = Literal[
    "idle", "preparing", "uploading", "waiting", "generating",
    "finalizing", "canceled", "failed", "succeeded",
]
RouteMode = Literal["cloud", "ollama", "llamacpp", "unknown"]
SendMode = Literal["enter_to_send", "ctrl_enter_to_send"]


@dataclass
class AppComposerDraftPayload:
    id: str
    text: str
    contextMode: str
    sendMode: SendMode
    restored: bool


@dataclass
class AppComposerContextAnchorPayload:
    id: str
    label: str
    type: str


@dataclass
class AppComposerAttachmentPayload:
    id: str
    name: str
    kind: str
    tokenCount: int
    preparationState: str
    contextLabel: str


@dataclass
class AppComposerContextPayload:
    anchor: AppComposerContextAnchorPayload | None
    items: list[AppComposerAttachmentPayload]
    totalTokens: int
    reviewAvailable: bool


@dataclass
class AppComposerModelOptionPayload:
    id: str
    label: str
    provider: str
    source: str
    active: bool
    ready: bool
    available: bool
    capabilities: list[str]


@dataclass
class AppComposerReasoningOptionPayload:
    id: str
    label: str
    description: str


@dataclass
class AppComposerReasoningPayload:
    level: str
    label: str
    options: list[AppComposerReasoningOptionPayload]


@dataclass
class AppComposerRoutePayload:
    mode: RouteMode
    provider: str
    modelId: str
    modelLabel: str
    modelOptions: list[AppComposerModelOptionPayload]
    reasoning: AppComposerReasoningPayload
    label: str
    available: bool
    canChange: bool
    modelValue: str | None = None


@dataclass
class AppComposerRequestPayload:
    id: str | None
    state: RequestState
    message: str
    canSend: bool
    canCancel: bool
    canRetry: bool


@dataclass
class AppComposerCapabilitiesPayload:
    attachments: bool
    contextReview: bool
    routeSelection: bool
    modelSelection: bool
    reasoningSelection: bool
    settingsShortcut: bool
    cancellation: bool


@dataclass
class AppComposerStatePayload:
    """The complete published snapshot, including the envelope fields the
    event bus stamps onto every topic's payload. No `theme` field - see the
    module docstring."""

    schemaVersion: int
    revision: int
    draft: AppComposerDraftPayload
    context: AppComposerContextPayload
    route: AppComposerRoutePayload
    request: AppComposerRequestPayload
    capabilities: AppComposerCapabilitiesPayload
    minCompatibleSchemaVersion: int | None = None
