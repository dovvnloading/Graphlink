"""Provider-neutral model metadata and task-routing helpers.

The settings UI and the request runtime used to pass around model IDs as opaque
strings.  This module keeps the public model ID deliberately small while giving
the rest of the application a stable place for readiness, capability, and
selection semantics.  Provider adapters can add richer metadata without making
the settings layer aware of a provider SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping


AUTO_MODEL = "auto"
INHERIT_MODEL = "inherit"

CAPABILITY_TEXT = "text"
CAPABILITY_CODE = "code"
CAPABILITY_VISION = "vision"
CAPABILITY_AUDIO = "audio"
CAPABILITY_TOOLS = "tools"
CAPABILITY_REASONING = "reasoning"
CAPABILITY_IMAGE = "image"


TASK_REQUIREMENTS = {
    "task_title": frozenset({CAPABILITY_TEXT}),
    "task_chat": frozenset({CAPABILITY_TEXT}),
    "task_chart": frozenset({CAPABILITY_TEXT, CAPABILITY_CODE}),
    "task_image_gen": frozenset({CAPABILITY_IMAGE}),
    "task_web_validate": frozenset({CAPABILITY_TEXT}),
    "task_web_summarize": frozenset({CAPABILITY_TEXT}),
}


@dataclass(frozen=True)
class ModelDescriptor:
    """A display and routing description for one model ID.

    ``ready`` is intentionally separate from ``available``: cloud catalog
    entries can be selectable even when their endpoint is currently offline,
    while a local model must be installed before it can be used.
    """

    model_id: str
    provider: str = ""
    ready: bool = True
    available: bool = True
    capabilities: frozenset[str] = field(default_factory=frozenset)
    source: str = "catalog"
    size_bytes: int | None = None
    context_length: int | None = None
    quantization: str = ""
    details: Mapping[str, object] = field(default_factory=dict)
    error: str = ""

    def supports(self, required: Iterable[str]) -> bool:
        required = set(required or ())
        if not required:
            return True
        # Unknown capability metadata should not make a model disappear from
        # the picker.  The runtime/provider remains the final authority.
        if not self.capabilities:
            return True
        return required.issubset(self.capabilities)

    @property
    def display_name(self) -> str:
        return self.model_id


@dataclass(frozen=True)
class ModelAssignment:
    """Persistable task assignment with explicit inheritance semantics."""

    mode: str = AUTO_MODEL
    model_id: str = ""

    @classmethod
    def from_value(cls, value) -> "ModelAssignment":
        if isinstance(value, Mapping):
            mode = str(value.get("mode", AUTO_MODEL) or AUTO_MODEL).strip().lower()
            model_id = normalize_model_id(value.get("model_id", value.get("model", "")))
            if mode == "explicit" and not model_id:
                mode = AUTO_MODEL
            if mode not in {AUTO_MODEL, INHERIT_MODEL, "explicit"}:
                mode = AUTO_MODEL
            return cls(mode, model_id)

        model_id = normalize_model_id(value)
        if not model_id or model_id.lower() in {AUTO_MODEL, INHERIT_MODEL}:
            return cls(AUTO_MODEL if model_id != INHERIT_MODEL else INHERIT_MODEL)
        return cls("explicit", model_id)

    def to_dict(self) -> dict[str, str]:
        return {"mode": self.mode, "model_id": self.model_id}


def normalize_model_id(value) -> str:
    return str(value or "").strip()


def normalize_assignments(values: Mapping | None) -> dict[str, ModelAssignment]:
    values = values if isinstance(values, Mapping) else {}
    return {str(task): ModelAssignment.from_value(value) for task, value in values.items()}


def assignment_values(values: Mapping | None) -> dict[str, dict[str, str]]:
    return {
        task: assignment.to_dict()
        for task, assignment in normalize_assignments(values).items()
    }


def _field(value, name: str, default=None):
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _as_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def ollama_descriptor(model, *, provider: str = "Ollama") -> ModelDescriptor:
    """Normalize an Ollama ``list()``/``show()`` result into a descriptor."""

    model_id = normalize_model_id(_field(model, "model") or _field(model, "name"))
    details = _field(model, "details", {}) or {}
    capabilities = set()
    raw_capabilities = _field(model, "capabilities") or _field(details, "capabilities", [])
    if isinstance(raw_capabilities, str):
        raw_capabilities = [raw_capabilities]
    for capability in raw_capabilities or ():
        normalized = str(capability).strip().lower().replace("-", "_")
        aliases = {
            "embedding": CAPABILITY_TEXT,
            "completion": CAPABILITY_TEXT,
            "image_generation": CAPABILITY_IMAGE,
            "image": CAPABILITY_VISION,
            "vision": CAPABILITY_VISION,
            "tool": CAPABILITY_TOOLS,
            "function_calling": CAPABILITY_TOOLS,
        }
        capabilities.add(aliases.get(normalized, normalized))

    family = str(_field(details, "family", "") or "").lower()
    if family:
        capabilities.add(CAPABILITY_TEXT)
        if "code" in family or "coder" in family:
            capabilities.add(CAPABILITY_CODE)
    if _field(details, "parameter_size"):
        # A model with Ollama details is at least a usable text model unless
        # the provider explicitly reports another modality.
        capabilities.add(CAPABILITY_TEXT)

    return ModelDescriptor(
        model_id=model_id,
        provider=provider,
        ready=True,
        available=True,
        capabilities=frozenset(capabilities),
        source="installed",
        size_bytes=_as_int(_field(model, "size")),
        context_length=_as_int(_field(details, "context_length")),
        quantization=str(_field(details, "quantization_level", "") or ""),
        details=dict(details) if isinstance(details, Mapping) else {},
    )


def sort_descriptors(descriptors: Iterable[ModelDescriptor]) -> list[ModelDescriptor]:
    unique: dict[tuple[str, str], ModelDescriptor] = {}
    for descriptor in descriptors or ():
        if not isinstance(descriptor, ModelDescriptor):
            continue
        key = (descriptor.provider.lower(), descriptor.model_id.lower())
        if key[1] and (key not in unique or descriptor.ready):
            unique[key] = descriptor
    return sorted(
        unique.values(),
        key=lambda item: (not item.ready, not item.available, item.model_id.lower()),
    )


def choose_auto_model(
    task: str,
    catalog: Iterable[ModelDescriptor],
    *,
    preferred_model: str = "",
) -> str:
    """Choose a deterministic ready model without provider-specific defaults."""

    candidates = [
        item
        for item in sort_descriptors(catalog)
        if item.ready and item.available and item.supports(TASK_REQUIREMENTS.get(task, ()))
    ]
    if not candidates:
        return ""
    preferred_model = normalize_model_id(preferred_model).lower()
    if preferred_model:
        for item in candidates:
            if item.model_id.lower() == preferred_model:
                return item.model_id
    # Prefer a known text/capability match, then stable alphabetical order.
    candidates.sort(key=lambda item: (not bool(item.capabilities), item.model_id.lower()))
    return candidates[0].model_id


def resolve_task_model(
    task: str,
    assignments: Mapping | None,
    catalog: Iterable[ModelDescriptor] = (),
    *,
    chat_model: str = "",
) -> str:
    """Resolve explicit, inherited, or automatic task routing."""

    normalized = normalize_assignments(assignments)
    assignment = normalized.get(task, ModelAssignment())
    if assignment.mode == "explicit" and assignment.model_id:
        return assignment.model_id
    if assignment.mode == INHERIT_MODEL:
        chat_assignment = normalized.get("task_chat", ModelAssignment())
        if chat_assignment.mode == "explicit" and chat_assignment.model_id:
            return chat_assignment.model_id
        if chat_model:
            return normalize_model_id(chat_model)
    return choose_auto_model(task, catalog, preferred_model=chat_model if task != "task_chat" else "")
