"""Shared lifecycle and context primitives for canvas utility operations."""

from dataclasses import dataclass, field
from enum import Enum
from threading import Event
from uuid import uuid4

from PySide6.QtCore import QObject, Signal


class UtilityKind(str, Enum):
    TAKEAWAY = "takeaway"
    EXPLAINER = "explainer"
    GROUP_SUMMARY = "group_summary"


class UtilityOperationState(str, Enum):
    PREPARING = "preparing"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"


@dataclass(frozen=True)
class UtilitySourceSnapshot:
    source_id: str
    source_type: str
    text: str
    x: float = 0.0
    y: float = 0.0
    revision: str = ""


@dataclass(frozen=True)
class UtilityContextSnapshot:
    operation_id: str
    chat_epoch: int
    sources: tuple[UtilitySourceSnapshot, ...]
    rendered_context: str
    estimated_tokens: int
    omitted_source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class UtilityResult:
    operation_id: str
    kind: UtilityKind
    content: str
    context: UtilityContextSnapshot
    provider_snapshot: dict = field(default_factory=dict)


@dataclass
class _Operation:
    operation_id: str
    kind: UtilityKind
    context: UtilityContextSnapshot
    state: UtilityOperationState = UtilityOperationState.PREPARING
    cancel_event: Event = field(default_factory=Event)


class UtilityOperationController(QObject):
    """Own operation identity, cancellation and stale-result guards."""

    operation_started = Signal(object)
    operation_state_changed = Signal(str, str)
    operation_finished = Signal(object)
    operation_failed = Signal(str, str)
    operation_cancelled = Signal(str)
    operation_stale = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._operations = {}

    def begin(self, kind, sources, chat_epoch=0, rendered_context="", estimated_tokens=0, omitted_source_ids=()):
        operation_id = uuid4().hex
        context = UtilityContextSnapshot(
            operation_id=operation_id,
            chat_epoch=int(chat_epoch),
            sources=tuple(sources),
            rendered_context=rendered_context,
            estimated_tokens=int(estimated_tokens),
            omitted_source_ids=tuple(omitted_source_ids),
        )
        operation = _Operation(operation_id, UtilityKind(kind), context)
        self._operations[operation_id] = operation
        self.operation_started.emit(operation)
        return operation_id

    def _set_state(self, operation_id, state):
        operation = self._operations.get(operation_id)
        if operation is None:
            return False
        operation.state = UtilityOperationState(state)
        self.operation_state_changed.emit(operation_id, operation.state.value)
        return True

    def mark_running(self, operation_id):
        return self._set_state(operation_id, UtilityOperationState.RUNNING)

    def is_current(self, operation_id, chat_epoch=None):
        operation = self._operations.get(operation_id)
        if operation is None:
            return False
        if operation.state in {
            UtilityOperationState.CANCELLED,
            UtilityOperationState.FAILED,
            UtilityOperationState.STALE,
            UtilityOperationState.SUCCEEDED,
        }:
            return False
        return chat_epoch is None or operation.context.chat_epoch == chat_epoch

    def cancellation_requested(self, operation_id):
        operation = self._operations.get(operation_id)
        return bool(operation and operation.cancel_event.is_set())

    def cancel(self, operation_id):
        operation = self._operations.get(operation_id)
        if operation is None or operation.state in {
            UtilityOperationState.SUCCEEDED,
            UtilityOperationState.FAILED,
            UtilityOperationState.CANCELLED,
            UtilityOperationState.STALE,
        }:
            return False
        operation.cancel_event.set()
        self._set_state(operation_id, UtilityOperationState.CANCELLED)
        self.operation_cancelled.emit(operation_id)
        return True

    def complete(self, operation_id, content, provider_snapshot=None):
        operation = self._operations.get(operation_id)
        if operation is None or not self.is_current(operation_id):
            return None
        result = UtilityResult(
            operation_id=operation_id,
            kind=operation.kind,
            content=str(content or ""),
            context=operation.context,
            provider_snapshot=dict(provider_snapshot or {}),
        )
        self._set_state(operation_id, UtilityOperationState.SUCCEEDED)
        self.operation_finished.emit(result)
        return result

    def fail(self, operation_id, message):
        operation = self._operations.get(operation_id)
        if operation is None or operation.state in {
            UtilityOperationState.SUCCEEDED,
            UtilityOperationState.CANCELLED,
            UtilityOperationState.STALE,
        }:
            return False
        self._set_state(operation_id, UtilityOperationState.FAILED)
        self.operation_failed.emit(operation_id, str(message))
        return True

    def mark_stale(self, operation_id):
        if not self._set_state(operation_id, UtilityOperationState.STALE):
            return False
        self.operation_stale.emit(operation_id)
        return True

    def get(self, operation_id):
        return self._operations.get(operation_id)

    def active_operations(self):
        terminal = {
            UtilityOperationState.SUCCEEDED,
            UtilityOperationState.FAILED,
            UtilityOperationState.CANCELLED,
            UtilityOperationState.STALE,
        }
        return tuple(operation for operation in self._operations.values() if operation.state not in terminal)


def ensure_persistent_id(item):
    persistent_id = getattr(item, "persistent_id", None)
    if not persistent_id:
        persistent_id = uuid4().hex
        item.persistent_id = persistent_id
    return persistent_id


def source_snapshot(item, text, revision=""):
    position = item.scenePos() if hasattr(item, "scenePos") else None
    return UtilitySourceSnapshot(
        source_id=ensure_persistent_id(item),
        source_type=type(item).__name__,
        text=str(text or ""),
        x=float(position.x()) if position is not None else 0.0,
        y=float(position.y()) if position is not None else 0.0,
        revision=str(revision or ""),
    )


def render_context(sources, max_chars=24000):
    """Render deterministic, bounded utility context and report omitted sources."""
    rendered = []
    omitted = []
    used = 0
    for index, source in enumerate(sources, 1):
        block = f"Source {index} ({source.source_type}):\n{source.text.strip()}\n"
        if rendered and used + len(block) > max_chars:
            omitted.extend(item.source_id for item in sources[index - 1:])
            break
        rendered.append(block)
        used += len(block)
    return "\n".join(rendered).strip(), tuple(omitted)
