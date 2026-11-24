"""Versioned, transport-neutral workflow contracts.

These are not the existing Graph Runner's state or a new execution engine.
WorkflowSpec is a snapshot: tuple collections, frozen nested records, pinned
adapter descriptors and canonical JSON configuration strings. Persist its
dataclass fields as JSON objects/arrays, not references to live scene nodes.
All record fields are explicit; nullable fields are serialized as JSON null.
File and image values identify immutable assets without opening them.

Side-effect classes describe the operation, not permission to perform it:
pure (deterministic computation, no external effects), local-read (reads local
state), cloud (remote service calls), executes (runs code/processes), writes
(modifies external or durable state). Adapters must not hide side effects in
prepare/fingerprint; declaring a class never authorizes execution.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal


type PortKind = Literal["text", "json", "table", "file", "image"]


type SideEffectClass = Literal["pure", "local-read", "cloud", "executes", "writes"]


type JsonScalar = str | int | float | bool | None


type JsonData = JsonScalar | list[JsonData] | dict[str, JsonData]


@dataclass(frozen=True, slots=True)
class PortSpec:
    """A named input or output; required means a value must be present."""

    name: str
    kind: PortKind
    required: bool


@dataclass(frozen=True, slots=True)
class PortSet:
    """Ordered input and output declarations, with unique names per direction."""

    inputs: tuple[PortSpec, ...]
    outputs: tuple[PortSpec, ...]


@dataclass(frozen=True, slots=True)
class AssetRef:
    """Content-addressed asset metadata, never an open file, path or URL."""

    asset_id: str
    sha256: str
    media_type: str
    filename: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class TextValue:
    """Unicode text, including an empty string."""

    kind: Literal["text"] = field(default="text", init=False)
    text: str


@dataclass(frozen=True, slots=True)
class JsonValue:
    """Any JSON value, including scalars, arrays and null."""

    kind: Literal["json"] = field(default="json", init=False)
    value: JsonData


@dataclass(frozen=True, slots=True)
class TableValue:
    """Ordered unique column names and rectangular rows of JSON scalar cells."""

    kind: Literal["table"] = field(default="table", init=False)
    columns: tuple[str, ...]
    rows: tuple[tuple[JsonScalar, ...], ...]


@dataclass(frozen=True, slots=True)
class FileValue:
    """A reference to a file asset; no implicit file reads."""

    kind: Literal["file"] = field(default="file", init=False)
    asset: AssetRef


@dataclass(frozen=True, slots=True)
class ImageValue:
    """A reference to an image asset; not interchangeable with a file port."""

    kind: Literal["image"] = field(default="image", init=False)
    asset: AssetRef


type PortValue = TextValue | JsonValue | TableValue | FileValue | ImageValue


@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    """Pinned adapter identity, interface and declared side-effect class."""

    adapter_id: str
    adapter_version: str
    ports: PortSet
    side_effect_class: SideEffectClass


@dataclass(frozen=True, slots=True)
class WorkflowInputSource:
    """A connection from a workflow-level input."""

    kind: Literal["input"] = field(default="input", init=False)
    port: str


@dataclass(frozen=True, slots=True)
class StepOutputSource:
    """A connection from the named output of another frozen step."""

    kind: Literal["step"] = field(default="step", init=False)
    step_id: str
    port: str


type InputSource = WorkflowInputSource | StepOutputSource


@dataclass(frozen=True, slots=True)
class InputBinding:
    """Bind one named step input to exactly one source."""

    port: str
    source: InputSource


@dataclass(frozen=True, slots=True)
class WorkflowOutput:
    """Expose a named typed workflow output from a source."""

    port: PortSpec
    source: InputSource


@dataclass(frozen=True, slots=True)
class WorkflowStepSpec:
    """One frozen step: adapter version, declared ports, config and connections."""

    step_id: str
    adapter: AdapterDescriptor
    config_json: str
    bindings: tuple[InputBinding, ...]


@dataclass(frozen=True, slots=True)
class WorkflowSpec:
    """Immutable version-1 snapshot. No references to live canvas state."""

    schema_version: Literal[1]
    workflow_id: str
    revision: str
    name: str
    inputs: tuple[PortSpec, ...]
    steps: tuple[WorkflowStepSpec, ...]
    outputs: tuple[WorkflowOutput, ...]


type RunStatus = Literal["pending", "running", "completed", "failed", "cancelled", "interrupted"]


type StepStatus = Literal["pending", "running", "completed", "failed", "cancelled", "blocked", "cached", "skipped", "interrupted"]


type EventKind = Literal["run-created", "run-started", "run-completed", "run-failed", "run-cancelled", "run-interrupted", "step-started", "step-progress", "step-completed", "step-failed", "step-cancelled", "step-blocked", "step-cached", "step-skipped", "step-interrupted"]


@dataclass(frozen=True, slots=True)
class WorkflowRunRecord:
    """A run's pinned spec, inputs, status and results; timestamps are UTC ISO 8601."""

    schema_version: Literal[1]
    run_id: str
    spec: WorkflowSpec
    spec_fingerprint: str
    status: RunStatus
    created_at: str
    started_at: str | None
    finished_at: str | None
    inputs: dict[str, PortValue]
    outputs: dict[str, PortValue]
    error: str | None


@dataclass(frozen=True, slots=True)
class WorkflowStepRecord:
    """One attempt at a step within a run, with fingerprint and typed results."""

    schema_version: Literal[1]
    run_id: str
    step_id: str
    attempt: int
    side_effect_class: SideEffectClass
    status: StepStatus
    fingerprint: str | None
    started_at: str | None
    finished_at: str | None
    outputs: dict[str, PortValue]
    error: str | None


@dataclass(frozen=True, slots=True)
class WorkflowEventRecord:
    """An append-only event, ordered by sequence within its run."""

    schema_version: Literal[1]
    run_id: str
    sequence: int
    timestamp: str
    kind: EventKind
    step_id: str | None
    attempt: int | None
    message: str
    data: JsonData


def freeze_config(config: Mapping[str, JsonData]) -> str:
    """Copy JSON configuration by value in a stable, immutable representation.

    Configuration must contain JSON data, not credentials, live objects or
    runtime handles. Adapters own its semantic validation.
    """
    return json.dumps(dict(config), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


# Public named wire definitions in dependency/readability order, consumed by
# build-time codegen only. The Protocol and prepared state are not wire values.
WIRE_TYPES = (
    PortKind,
    SideEffectClass,
    JsonScalar,
    JsonData,
    PortSpec,
    PortSet,
    AssetRef,
    TextValue,
    JsonValue,
    TableValue,
    FileValue,
    ImageValue,
    PortValue,
    AdapterDescriptor,
    WorkflowInputSource,
    StepOutputSource,
    InputSource,
    InputBinding,
    WorkflowOutput,
    WorkflowStepSpec,
    WorkflowSpec,
    RunStatus,
    StepStatus,
    EventKind,
    WorkflowRunRecord,
    WorkflowStepRecord,
    WorkflowEventRecord,
)
