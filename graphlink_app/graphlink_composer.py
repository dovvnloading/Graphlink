"""Composer domain state and request lifecycle primitives.

The Composer used to be represented by a handful of widget fields plus flags on
ChatWindow.  These small value objects provide one stable contract for the UI,
request preparation, and future streaming transports without coupling the
domain to graph widgets or a provider SDK.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from PySide6.QtCore import QObject, Signal


class ComposerRequestState(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    UPLOADING = "uploading"
    WAITING = "waiting"
    GENERATING = "generating"
    FINALIZING = "finalizing"
    CANCELED = "canceled"
    FAILED = "failed"
    SUCCEEDED = "succeeded"


@dataclass
class ComposerAttachment:
    attachment_id: str
    path: str
    name: str
    kind: str
    preparation_state: str = "ready"
    error: str = ""
    token_estimate: int = 0
    byte_size: int = 0
    context_label: str = ""
    is_temp: bool = False

    @classmethod
    def from_mapping(cls, item: dict) -> "ComposerAttachment":
        return cls(
            attachment_id=str(item.get("attachment_id") or uuid4().hex),
            path=str(item.get("path") or ""),
            name=str(item.get("name") or "Attachment"),
            kind=str(item.get("kind") or "document"),
            preparation_state=str(item.get("preparation_state") or "ready"),
            error=str(item.get("error") or ""),
            token_estimate=int(item.get("token_count") or item.get("token_estimate") or 0),
            byte_size=int(item.get("byte_size") or 0),
            context_label=str(item.get("context_label") or ""),
            is_temp=bool(item.get("is_temp", False)),
        )

    def to_mapping(self) -> dict:
        result = asdict(self)
        result["token_count"] = result.pop("token_estimate")
        return result


@dataclass
class ComposerDraft:
    draft_id: str = field(default_factory=lambda: uuid4().hex)
    text: str = ""
    branch_anchor_id: str = ""
    context_mode: str = "branch"
    context_refs: list[str] = field(default_factory=list)
    attachments: list[ComposerAttachment] = field(default_factory=list)
    send_mode: str = "enter_to_send"
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    restored: bool = False

    def to_mapping(self) -> dict:
        return {
            "draft_id": self.draft_id,
            "text": self.text,
            "branch_anchor_id": self.branch_anchor_id,
            "context_mode": self.context_mode,
            "context_refs": list(self.context_refs),
            "attachments": [item.to_mapping() for item in self.attachments],
            "send_mode": self.send_mode,
            "updated_at": self.updated_at,
            "restored": self.restored,
        }


@dataclass(frozen=True)
class ComposerRequestSnapshot:
    request_id: str
    draft_id: str
    text: str
    branch_anchor_id: str
    context_mode: str
    attachment_paths: tuple[str, ...]
    created_at: str


class ComposerController(QObject):
    """Owns draft identity and request transitions for one Composer surface."""

    draftChanged = Signal(object)
    stateChanged = Signal(str, str)
    requestStarted = Signal(str)
    requestFinished = Signal(str, str)
    requestFailed = Signal(str, str)
    requestCancelled = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.draft = ComposerDraft()
        self.state = ComposerRequestState.IDLE
        self.state_message = ""
        self.active_snapshot: ComposerRequestSnapshot | None = None

    @property
    def active_request_id(self) -> str | None:
        return self.active_snapshot.request_id if self.active_snapshot else None

    def update_text(self, text: str):
        self.draft.text = str(text or "")
        self._touch_draft()

    def set_branch(self, anchor_id: str = "", context_mode: str = "branch"):
        self.draft.branch_anchor_id = str(anchor_id or "")
        self.draft.context_mode = str(context_mode or "branch")
        self._touch_draft()

    def set_context_refs(self, refs):
        self.draft.context_refs = [str(ref) for ref in (refs or []) if str(ref)]
        self._touch_draft()

    def set_attachments(self, attachments):
        self.draft.attachments = [
            item if isinstance(item, ComposerAttachment) else ComposerAttachment.from_mapping(item)
            for item in (attachments or [])
        ]
        self._touch_draft()

    def begin_request(self, *, text: str, attachments: list[dict] | None = None) -> str:
        self.update_text(text)
        if attachments is not None:
            self.set_attachments(attachments)
        request_id = uuid4().hex
        self.active_snapshot = ComposerRequestSnapshot(
            request_id=request_id,
            draft_id=self.draft.draft_id,
            text=self.draft.text,
            branch_anchor_id=self.draft.branch_anchor_id,
            context_mode=self.draft.context_mode,
            attachment_paths=tuple(item.path for item in self.draft.attachments),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.set_state(ComposerRequestState.PREPARING, "Preparing context")
        self.requestStarted.emit(request_id)
        return request_id

    def mark_started(self, request_id: str, message: str = "Waiting for model") -> bool:
        if request_id != self.active_request_id:
            return False
        self.set_state(ComposerRequestState.WAITING, message)
        return True

    def is_current(self, request_id: str | None) -> bool:
        return bool(request_id and request_id == self.active_request_id)

    def complete(self, request_id: str, message: str = "") -> bool:
        if not self.is_current(request_id):
            return False
        self.set_state(ComposerRequestState.SUCCEEDED, message)
        self.requestFinished.emit(request_id, message)
        self.active_snapshot = None
        self.set_state(ComposerRequestState.IDLE, "")
        return True

    def fail(self, request_id: str | None, message: str) -> bool:
        if request_id and not self.is_current(request_id):
            return False
        active_id = request_id or self.active_request_id or ""
        self.set_state(ComposerRequestState.FAILED, message)
        self.requestFailed.emit(active_id, message)
        self.active_snapshot = None
        return True

    def cancel(self, request_id: str | None) -> bool:
        if request_id and not self.is_current(request_id):
            return False
        active_id = request_id or self.active_request_id or ""
        self.set_state(ComposerRequestState.CANCELED, "Request canceled")
        self.requestCancelled.emit(active_id)
        self.active_snapshot = None
        return True

    def clear_after_success(self):
        self.draft.text = ""
        self.draft.attachments = []
        self.draft.restored = False
        self._touch_draft()

    def serialize_draft(self) -> dict:
        return self.draft.to_mapping()

    def restore_draft(self, payload: dict | None) -> ComposerDraft:
        payload = payload if isinstance(payload, dict) else {}
        self.draft = ComposerDraft(
            draft_id=str(payload.get("draft_id") or uuid4().hex),
            text=str(payload.get("text") or ""),
            branch_anchor_id=str(payload.get("branch_anchor_id") or ""),
            context_mode=str(payload.get("context_mode") or "branch"),
            context_refs=[str(ref) for ref in payload.get("context_refs", []) if str(ref)],
            attachments=[ComposerAttachment.from_mapping(item) for item in payload.get("attachments", [])],
            send_mode=str(payload.get("send_mode") or "enter_to_send"),
            updated_at=str(payload.get("updated_at") or datetime.now(timezone.utc).isoformat()),
            restored=bool(payload.get("text") or payload.get("attachments")),
        )
        self.draftChanged.emit(self.draft)
        return self.draft

    def set_state(self, state: ComposerRequestState, message: str = ""):
        self.state = ComposerRequestState(state)
        self.state_message = str(message or "")
        self.stateChanged.emit(self.state.value, self.state_message)

    def _touch_draft(self):
        self.draft.updated_at = datetime.now(timezone.utc).isoformat()
        self.draftChanged.emit(self.draft)
