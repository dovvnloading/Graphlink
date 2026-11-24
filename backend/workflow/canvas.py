"""Canvas bridge for pinned, pure reusable workflows."""
from __future__ import annotations

import hashlib
import json

from backend.domain.model import SceneError
from backend.domain.node_states import ModelState, NoteState, PluginStateSnapshot, PersistedPluginState
from backend.workflow.contracts import (
    InputBinding, PortSpec, StepOutputSource, WorkflowInputSource, WorkflowOutput,
    WorkflowSpec, WorkflowStepSpec, freeze_config,
)
from backend.workflow.library import (
    FORMAT, canonical, configured_spec, library_registry, make_document, ordered_steps, typed_inputs,
)

KIND = "graph_runner.workflow"


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def selected_workflow(document, ids, name="Workflow", outputs=None, parameters=None):
    """Freeze explicit note bindings, never the content of their inputs."""
    if not isinstance(ids, list) or not ids or len(ids) > 200 or len(set(ids)) != len(ids):
        raise ValueError("Select between 1 and 200 distinct connected nodes.")
    nodes = [document._plain_note(node_id) for node_id in ids]
    if any(node.state.model_text_input is not None or node.state.is_table_source for node in nodes):
        raise ValueError("Only plain text notes and their pure text bindings can be captured.")
    selected = set(ids)
    links = {node.id: node.state.text_input_source_id for node in nodes if node.state.text_input_source_id}
    if not links:
        raise ValueError("Select at least one connected text operation.")
    # The selection is a single weakly connected component of explicit data links.
    seen = {ids[0]}
    while True:
        expanded = seen | {a for a, b in links.items() if b in seen} | {
            b for a, b in links.items() if a in seen and b in selected}
        if expanded == seen:
            break
        seen = expanded
    if seen != selected:
        raise ValueError("Select a single connected set of text operations and their inputs.")
    step_ids = {node.id: f"step_{i + 1}" for i, node in enumerate(n for n in nodes if n.id in links)}
    inputs = {}
    steps = []
    registry = library_registry()
    for node in nodes:
        if node.id not in links:
            continue
        source_id = links[node.id]
        if source_id in step_ids:
            source = StepOutputSource(step_ids[source_id], "text")
        else:
            if source_id not in document.nodes:
                raise ValueError("A selected connection has a missing source.")
            inputs.setdefault(source_id, f"input_{len(inputs) + 1}")
            source = WorkflowInputSource(inputs[source_id])
        config = freeze_config({"operation": node.state.text_input_operation})
        adapter = registry.describe("note.text", "1", config)
        steps.append(WorkflowStepSpec(step_ids[node.id], adapter, config, (InputBinding("text", source),)))
    candidates = [WorkflowOutput(PortSpec(f"output_{i + 1}", "text", True),
                                 StepOutputSource(step.step_id, "text")) for i, step in enumerate(steps)]
    if outputs is not None:
        if not isinstance(outputs, list) or not set(outputs) <= {o.port.name for o in candidates}:
            raise ValueError("Choose valid workflow outputs.")
        candidates = [o for o in candidates if o.port.name in outputs]
    spec = WorkflowSpec(1, "preview", "preview", name, tuple(PortSpec(p, "text", True) for p in inputs.values()),
                        tuple(steps), tuple(candidates))
    return make_document(spec, parameters or [])


def data(node):
    if node.kind != KIND or not isinstance(node.state, (PluginStateSnapshot, PersistedPluginState)):
        raise SceneError("Select a Workflow node.")
    value = node.state.data
    if (not isinstance(value.get("document"), dict) or not isinstance(value.get("parameters"), dict)
            or not isinstance(value.get("inputs"), dict) or not isinstance(value.get("outputs"), dict)
            or not isinstance(value.get("lastInputHash"), str)):
        raise SceneError("The Workflow node has no pinned version.")
    return value


def new_state(document, version):
    if document.get("format") != FORMAT:
        raise ValueError("Built-in starters run in the detached workflow inspector, not on the canvas.")
    return {"document": document, "version": version, "inputs": {}, "sources": {}, "parameters": {},
            "outputs": {}, "lastInputHash": "", "childRunId": ""}


def insert(document, frozen, version, x, y):
    return document.add_plugin_node(KIND, x, y, None, title=frozen["spec"]["name"],
                                    state=PluginStateSnapshot.from_data(new_state(frozen, version))).id


def dependencies(node):
    value = data(node)
    sources = value.get("sources", {})
    if (not isinstance(sources, dict) or len(sources) > 64
            or any(not isinstance(source, str) or not source for source in sources.values())):
        raise SceneError("The workflow source bindings are invalid.")
    return tuple(dict.fromkeys(sources[key] for key in sorted(sources)))


def contains_dependency(document, source_id, target_id):
    pending, seen = [source_id], set()
    while pending:
        current = pending.pop()
        if current == target_id:
            return True
        if current in seen or current not in document.nodes:
            continue
        seen.add(current)
        node = document.nodes[current]
        if node.kind == KIND:
            pending.extend(dependencies(node))
        elif isinstance(node.state, NoteState):
            binding = node.state.model_text_input
            pending.append(binding.source_id if binding else node.state.text_input_source_id)
        elif isinstance(node.state, ModelState):
            pending.append(node.state.model_binding.source_id)
    return False


def configure(document, node, inputs, parameters, sources):
    old = data(node)
    spec = configured_spec(old["document"], parameters)
    if not isinstance(inputs, dict) or not inputs.keys() <= {p.name for p in spec.inputs}:
        raise ValueError("Unknown workflow input.")
    typed_inputs(spec, inputs, require_all=False)
    if not isinstance(sources, dict) or len(sources) > 64 or set(inputs) & set(sources):
        raise ValueError("Choose a literal or connected source for each workflow input.")
    text_ports = {p.name for p in spec.inputs if p.kind == "text"}
    if not sources.keys() <= text_ports:
        raise ValueError("Only text inputs can bind to a canvas text source.")
    for source in sources.values():
        if not isinstance(source, str):
            raise ValueError("Choose a valid workflow source.")
        document._text_output(source)
        if contains_dependency(document, source, node.id):
            raise ValueError("A workflow connection cannot form a cycle.")
    if len(canonical(inputs).encode()) > 1_000_000:
        raise ValueError("Workflow inputs are too large.")
    if old["inputs"] != inputs or old["parameters"] != parameters or old.get("sources", {}) != sources:
        old.update(inputs=inputs, sources=sources, parameters=parameters, outputs={}, lastInputHash="", childRunId="")
        node.state = PluginStateSnapshot.from_data(old)


def prepare(document, node):
    try:
        value = data(node)
        spec = configured_spec(value["document"], value["parameters"])
        inputs = dict(value["inputs"])
        dependencies(node)
        sources = value.get("sources", {})
        if set(inputs) & set(sources) or not sources.keys() <= {p.name for p in spec.inputs if p.kind == "text"}:
            raise ValueError("Invalid workflow input bindings.")
        for port, source in sources.items():
            inputs[port] = document._text_output(source)
        typed_inputs(spec, inputs)
        if len(canonical(inputs).encode()) > 1_000_000:
            raise ValueError("Workflow inputs are too large.")
        return spec, inputs, digest([value["document"], value["parameters"], inputs])
    except (ValueError, KeyError, TypeError) as exc:
        raise SceneError("Configure valid workflow inputs and parameters before running.") from exc


def config_hash(node):
    value = data(node)
    return digest([value["document"], value["parameters"], sorted(value.get("sources", {}))])


def output_hash(node):
    value = data(node)
    return digest(value["outputs"])


def text_output(node):
    value = data(node)
    spec = value["document"]["spec"]
    ports = [output["port"]["name"] for output in spec["outputs"] if output["port"]["kind"] == "text"]
    if len(ports) != 1:
        raise SceneError("A Workflow text connection requires exactly one exposed text output.")
    output = value["outputs"].get(ports[0])
    if output is None:
        return ""
    if output.get("kind") != "text" or not isinstance(output.get("text"), str):
        raise SceneError("The saved workflow output is invalid.")
    return output["text"]


def remap_sources(document, ids):
    for node in document.nodes.values():
        if node.kind != KIND:
            continue
        try:
            value = data(node)
            sources = value.get("sources", {})
            if not isinstance(sources, dict):
                continue
            # Missing saved node IDs stay invalid; never alias a reminted ID.
            value["sources"] = {key: ids.get(source, "missing") for key, source in sources.items()}
            node.state = PluginStateSnapshot.from_data(value)
        except (SceneError, ValueError, TypeError):
            continue


def unpack(document, frozen, inputs, parameters, x, y, connected=None):
    """Expand supported captured text adapters back into ordinary editable notes."""
    spec = configured_spec(frozen, parameters)
    if any(s.adapter.adapter_id != "note.text" for s in spec.steps):
        raise ValueError("This workflow contains adapters without editable canvas equivalents.")
    if any(p.kind != "text" for p in spec.inputs):
        raise ValueError("Only text inputs can be unpacked onto the canvas.")
    connected = connected or {}
    if not isinstance(connected, dict) or not connected.keys() <= {p.name for p in spec.inputs}:
        raise ValueError("Unknown connected workflow input.")
    sources, steps = {}, {}
    width, height = document.kind_fallback_footprint("note")

    def add(text):
        nx, ny = document.find_free_position(x, y, width, height)
        node = document.add_note(nx, ny)
        node.content = text
        return node

    for port in spec.inputs:
        value = inputs.get(port.name, "")
        if not isinstance(value, str):
            raise ValueError("A text input must be text.")
        input_node = add(value)
        if port.name in connected:
            document.bind_note_text_input(connected[port.name], input_node.id, "copy")
        sources[port.name] = input_node.id
    for step in ordered_steps(spec):
        source = step.bindings[0].source
        source_id = sources[source.port] if isinstance(source, WorkflowInputSource) else steps[source.step_id]
        node = add("")
        document.bind_note_text_input(source_id, node.id, json.loads(step.config_json)["operation"])
        steps[step.step_id] = node.id
    return list(sources.values()) + list(steps.values())
