"""Version-one adapters for Graph Runner's existing scene/journal format.

Keep legacy hashes and step dicts instead of translating saved runs to
WorkflowSpec/PortValue. Pinning and inspection are read-only. Only predictable
local projections may evaluate on a private scene copy. Approved execution
uses host services, never implicit permissions or an implicit provider.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from backend.domain.model import SceneError, SceneNode
from backend.domain.node_states import ModelState, NoteState
from backend.domain.graph_model_binding import validate_local_model_identity
from backend.domain.nodes_content import _text_digest
from backend.domain.table_workflows import _current_result, _fingerprint, table_output
from graphlink_plugins.mcp_connect.core import (
    configured as mcp_configured, configuration as mcp_configuration, config_hash as mcp_config_hash,
    dependencies as mcp_dependencies, output_hash as mcp_output_hash, prepare as prepare_mcp,
)
from graphlink_plugins.scientific.graph_run_adapter import (
    configured as science_configured, config_hash as science_config_hash,
    dependencies as science_dependencies, operation_id as science_operation_id,
    output_hash as science_output_hash, prepare as prepare_science,
)

from .model_note_adapter import ModelNoteAdapter
from .registry import builtin_registry

if TYPE_CHECKING:
    from backend.domain.graph import SceneDocument

Step = dict[str, Any]


class GraphExecutionContext(Protocol):
    """Host-owned services for the approved legacy run."""

    async def run_model(self, step: Step) -> None: ...
    async def scientific(self, step: Step) -> None: ...
    async def tool(self, step: Step) -> None: ...
    async def deterministic(self, step: Step) -> None: ...
    def recover_scientific(self, step: Step) -> bool: ...


@dataclass(frozen=True)
class GraphBinding:
    source_id: str
    operation: str
    sources: tuple[str, ...]
    error: str = ""


@dataclass(frozen=True)
class GraphStepAdapter:
    pin: Callable[[SceneDocument, Step], None]
    inspect: Callable[[SceneDocument, Step], None]
    evaluate: Callable[[SceneDocument, Step], None]
    output_digest: Callable[[SceneNode], str]
    match: Callable[[SceneDocument, SceneNode, Step], None]
    binding: Callable[[SceneDocument, SceneNode], GraphBinding | None]
    run: Callable[[GraphExecutionContext, Step], Awaitable[None]]
    predictable: bool = False
    sync_entry: bool = False
    fan_in: bool = False
    local_model: bool = False
    pin_preview: Callable[[SceneNode, Step, Any], None] | None = None
    recover_cancelled: Callable[[GraphExecutionContext, Step], bool] | None = None


def _kind(node: SceneNode) -> str:
    if mcp_configured(node):
        return "tool"
    if science_configured(node):
        return "scientific"
    return "table" if isinstance(node.state, NoteState) and node.state.is_table_source else node.kind


def _binding(node: SceneNode) -> tuple[str, str] | None:
    if isinstance(node.state, ModelState) and node.state.model_binding.source_id:
        return node.state.model_binding.source_id, "model"
    if isinstance(node.state, NoteState) and node.state.text_input_source_id:
        return node.state.text_input_source_id, node.state.text_input_operation
    output = table_output(node)
    if output is not None:
        return output.source_id, "group_sum"
    return None


def _pin_mcp(document: SceneDocument, step: Step) -> None:
    node = document.nodes[step["nodeId"]]
    config = mcp_configuration(node)
    step["configHash"] = mcp_config_hash(node)
    step["targetHash"] = mcp_output_hash(node)
    step["serverId"] = config["serverId"]
    step["toolName"] = config["toolName"]


def _pin_science(document: SceneDocument, step: Step) -> None:
    node = document.nodes[step["nodeId"]]
    step["configHash"] = science_config_hash(node)
    step["targetHash"] = science_output_hash(node)


def _pin_model(document: SceneDocument, step: Step) -> None:
    node = document.nodes[step["nodeId"]]
    if not isinstance(node.state, ModelState):
        raise SceneError("The Model node has an invalid configuration.")
    binding = node.state.model_binding
    validate_local_model_identity(binding.provider_name, binding.model_id)
    if binding.version != 1 or not binding.binding_id:
        raise SceneError("The Model input version is unsupported.")
    # A saved inspector must not depend on today's possibly-rebound node.
    # The provider/model identifiers are non-secret metadata pinned to
    # this run's configuration, just like the existing hashes.
    step["provider"] = binding.provider_name
    step["modelId"] = binding.model_id
    # Runtime node IDs are reminted after restoring a workspace. Pin the
    # durable binding identity and provider/model instead; _matching_node
    # separately verifies the remapped sourceId before every execution.
    # Including source_id here would reject a safe post-restart resume.
    step["configHash"] = _fingerprint([
        "model_text_v1", binding.provider_name, binding.model_id,
        binding.version, binding.binding_id,
    ])
    step["targetHash"] = _text_digest(node.content)


def _pin_note(document: SceneDocument, step: Step) -> None:
    node = document.nodes[step["nodeId"]]
    state = document._plain_note(step["nodeId"]).state
    if step["operation"] not in ("copy", "word_count"):
        raise SceneError("Choose Copy text or Word count for this input.")
    step["configHash"] = _fingerprint([node.kind, step["operation"]])
    step["targetHash"] = _fingerprint([node.content, state.text_input_last_source_sha256])


def _pin_table(document: SceneDocument, step: Step) -> None:
    node = document.nodes[step["nodeId"]]
    _, output = document._require_table_output(step["nodeId"])
    step["configHash"] = _fingerprint([
        node.kind, step["operation"], output.label_column, output.value_column,
    ])
    step["targetHash"] = _fingerprint([output.current.version, output.current.source_hash, _current_result(node)])


def _finish_inspection(step: Step, digest: str, last_digest: str) -> None:
    step["inputHash"] = digest
    if digest == last_digest:
        step.update(status="current", detail="Input is up to date.")
    else:
        step.update(status="ready", detail="Input has changed." if last_digest else "Input has not run yet.")


def _inspect_mcp(document: SceneDocument, step: Step) -> None:
    node = document.nodes[step["nodeId"]]
    prepared = prepare_mcp(document, node)
    step["inputHash"] = prepared.input_hash
    step.update(
        status="current" if prepared.current else "ready",
        detail="Input is up to date." if prepared.current else "MCP inputs are ready for review.",
    )
    return


def _inspect_science(document: SceneDocument, step: Step) -> None:
    node = document.nodes[step["nodeId"]]
    _, _, _, digest, same = prepare_science(document, node, lambda _pack: True)
    step["inputHash"] = digest
    step.update(
        status="current" if same else "ready",
        detail="Input is up to date." if same else "Scientific inputs are ready.",
    )
    return


def _inspect_model(document: SceneDocument, step: Step) -> None:
    source_id = step["sourceId"]
    node = document.nodes[step["nodeId"]]
    if not isinstance(node.state, ModelState):
        raise SceneError("This is not a Model node.")
    binding = node.state.model_binding
    document.require_current_model_source(source_id, check_text_inputs=True)
    text = document._text_output(source_id)
    if not text.strip() or len(text.encode("utf-8", errors="surrogatepass")) > 65536:
        raise SceneError("Model input must contain text and be at most 64 KiB.")
    digest = _text_digest(text)
    last_digest = binding.last_input_hash
    if (binding.last_output_hash and node.content and
            _text_digest(node.content) != binding.last_output_hash):
        raise SceneError("Model output has manual edits; keep them or restore the previous version.")
    _finish_inspection(step, digest, last_digest)
    if isinstance(node.state, ModelState):
        # Preview exposes the actual selected provider/model, without ever
        # revealing the prompt or guessing its result before dispatch.
        step["detail"] = (
            f"Local Ollama · {node.state.model_binding.model_id}. {step['detail']} "
            "Verify custom model aliases are not cloud-routed."
        )


def _inspect_note(document: SceneDocument, step: Step) -> None:
    node_id, source_id = step["nodeId"], step["sourceId"]
    state = document._plain_note(node_id).state
    # Model outputs are durable text, but old cached replies cannot be
    # treated as fresh downstream inputs after their upstream source
    # changes. Keep the deterministic runner local-only: block instead
    # of attempting or implying an unapproved model rerun.
    document.require_current_model_source(source_id)
    if step["operation"] not in ("copy", "word_count"):
        raise SceneError("Choose Copy text or Word count for this input.")
    digest = _text_digest(document._text_output(source_id))
    last_digest = state.text_input_last_source_sha256
    _finish_inspection(step, digest, last_digest)


def _inspect_table(document: SceneDocument, step: Step) -> None:
    node_id, source_id = step["nodeId"], step["sourceId"]
    _, output = document._require_table_output(node_id)
    digest = document._read_table(source_id).fingerprint(output.label_column, output.value_column)
    last_digest = output.current.source_hash
    # Leave edited, up-to-date reports intact. A stale projection needs
    # permission to replace its result under the normal table output rules.
    if digest != last_digest:
        document._editable_table_output(node_id)
    _finish_inspection(step, digest, last_digest)


def _evaluate_note(document: SceneDocument, step: Step) -> None:
    document.run_note_text_input(step["nodeId"])


def _evaluate_table(document: SceneDocument, step: Step) -> None:
    document.run_table_output(step["nodeId"])


def _evaluate_async(document: SceneDocument, step: Step) -> None:
    raise SceneError("This step requires its explicit asynchronous execution adapter.")


def _ordinary_digest(node: SceneNode) -> str:
    value = node.content if node.kind in {"note", "model"} else _current_result(node)
    return _text_digest(value) if isinstance(value, str) else _fingerprint(value)


def _match_ordinary(document: SceneDocument, node: SceneNode, step: Step) -> None:
    if _binding(node) != (step["sourceId"], step["operation"]):
        raise SceneError("The connection changed since the preview. Preview the branch again.")


def _match_mcp(document: SceneDocument, node: SceneNode, step: Step) -> None:
    if (not mcp_configured(node) or step["operation"] != "tool"
            or mcp_dependencies(document, node) != step_sources(step)):
        raise SceneError("The MCP inputs changed since preview. Preview the branch again.")


def _match_science(document: SceneDocument, node: SceneNode, step: Step) -> None:
    if (not science_configured(node)
            or science_operation_id(node) != step["operation"]
            or science_dependencies(document, node) != step_sources(step)):
        raise SceneError("The scientific inputs changed since preview. Preview the branch again.")


def _ordinary_binding(document: SceneDocument, node: SceneNode) -> GraphBinding | None:
    binding = _binding(node)
    if binding is None:
        return None
    source, operation = binding
    return GraphBinding(source, operation, (source,))


def _mcp_binding(document: SceneDocument, node: SceneNode) -> GraphBinding:
    try:
        parents = mcp_dependencies(document, node)
    except SceneError as exc:
        return GraphBinding(node.id, "tool", (), str(exc))
    return GraphBinding(parents[0] if parents else node.id, "tool", parents)


def _science_binding(document: SceneDocument, node: SceneNode) -> GraphBinding:
    parents = science_dependencies(document, node)
    operation = science_operation_id(node)
    return GraphBinding(parents[0] if parents else "", operation, parents)


def _pin_mcp_preview(node: SceneNode, step: Step, runtime: Any) -> None:
    if runtime is None:
        raise SceneError("MCP Connect is unavailable in this session. Reopen the graph.")
    runtime.pin_step(node, step)


_TABLE = GraphStepAdapter(
    _pin_table, _inspect_table, _evaluate_table, _ordinary_digest,
    _match_ordinary, _ordinary_binding, lambda context, step: context.deterministic(step),
    predictable=True, sync_entry=True,
)
_NOTE = GraphStepAdapter(
    _pin_note, _inspect_note, _evaluate_note, _ordinary_digest,
    _match_ordinary, _ordinary_binding, lambda context, step: context.deterministic(step),
    predictable=True, sync_entry=True,
)
_MODEL = GraphStepAdapter(
    _pin_model, _inspect_model, _evaluate_async, _ordinary_digest,
    _match_ordinary, _ordinary_binding, lambda context, step: context.run_model(step),
    sync_entry=True, local_model=True,
)
_SCIENCE = GraphStepAdapter(
    _pin_science, _inspect_science, _evaluate_async, science_output_hash,
    _match_science, _science_binding, lambda context, step: context.scientific(step),
    fan_in=True, recover_cancelled=lambda context, step: context.recover_scientific(step),
)
_MCP = GraphStepAdapter(
    _pin_mcp, _inspect_mcp, _evaluate_async, mcp_output_hash,
    _match_mcp, _mcp_binding, lambda context, step: context.tool(step),
    fan_in=True, pin_preview=_pin_mcp_preview,
)

# The old wire kind is the compatibility selector, not a new wire field.
# All legacy steps are pinned to this v1 implementation.
GRAPH_STEP_ADAPTERS = builtin_registry().with_entries((
    ("note", "1", _NOTE),
    ("table", "1", _TABLE),
    ("chart", "1", _TABLE),
    ("artifact", "1", _TABLE),
    ("model", "1", _MODEL),
    ("scientific", "1", _SCIENCE),
    ("tool", "1", _MCP),
    ("model-note", "1", ModelNoteAdapter()),
))


def adapter_for_step(step: Step) -> GraphStepAdapter:
    try:
        adapter = GRAPH_STEP_ADAPTERS.resolve(step["kind"], "1")
    except KeyError as exc:
        raise SceneError("The connection changed since the preview. Preview the branch again.") from exc
    if not isinstance(adapter, GraphStepAdapter):
        raise SceneError("The connection changed since the preview. Preview the branch again.")
    return adapter


def model_note_adapter() -> ModelNoteAdapter:
    adapter = GRAPH_STEP_ADAPTERS.resolve("model-note", "1")
    if not isinstance(adapter, ModelNoteAdapter):
        raise RuntimeError("The Model note adapter is unavailable.")
    return adapter


def discover_binding(document: SceneDocument, node: SceneNode) -> GraphBinding | None:
    kind = _kind(node)
    try:
        adapter = GRAPH_STEP_ADAPTERS.resolve(kind, "1")
    except KeyError:
        return None
    if not isinstance(adapter, GraphStepAdapter):
        return None
    return adapter.binding(document, node)


def step_sources(step: Step) -> tuple[str, ...]:
    """Every prerequisite, including a scientific operation's typed fan-in."""
    return tuple(step.get("sourceIds", (step["sourceId"],)))


def _config_and_target(document: SceneDocument, step: Step) -> None:
    """Pin output and configuration without requiring a future model reply."""
    adapter_for_step(step).pin(document, step)


def _inspect(document: SceneDocument, step: Step) -> None:
    """Read the actual operands, configuration and destination before one write."""
    adapter = adapter_for_step(step)
    adapter.pin(document, step)
    adapter.inspect(document, step)


def _evaluate(document: SceneDocument, step: Step) -> None:
    adapter_for_step(step).evaluate(document, step)


def _matching_node(document: SceneDocument, step: Step) -> SceneNode:
    node = document.nodes.get(step["nodeId"])
    if node is None or _kind(node) != step["kind"]:
        raise SceneError("The connection changed since the preview. Preview the branch again.")
    adapter_for_step(step).match(document, node, step)
    return node


def _output_digest(node: SceneNode) -> str:
    try:
        adapter = GRAPH_STEP_ADAPTERS.resolve(_kind(node), "1")
    except KeyError:
        return _ordinary_digest(node)
    return adapter.output_digest(node) if isinstance(adapter, GraphStepAdapter) else _ordinary_digest(node)


def pin_output_version(document: SceneDocument, step: dict[str, Any]) -> None:
    """Capture the table's version during execution, never at inspection time."""
    node = document.nodes.get(step["nodeId"])
    output = table_output(node) if node is not None else None
    if output is not None:
        step["outputVersion"] = output.current.version
