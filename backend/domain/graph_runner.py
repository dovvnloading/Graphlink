"""Preview and execute the canvas's explicit data bindings in dependency order."""

from __future__ import annotations

import copy
from collections import defaultdict, deque
from typing import TYPE_CHECKING

from backend.domain.model import SceneError, SceneNode
from backend.domain.node_states import NoteState
from backend.domain.nodes_content import _text_digest
from backend.domain.table_workflows import _current_result, _fingerprint, table_output

if TYPE_CHECKING:
    from backend.domain.graph import SceneDocument


def _kind(node: SceneNode) -> str:
    return "table" if isinstance(node.state, NoteState) and node.state.is_table_source else node.kind


def _binding(node: SceneNode) -> tuple[str, str] | None:
    if isinstance(node.state, NoteState) and node.state.text_input_source_id:
        return node.state.text_input_source_id, node.state.text_input_operation
    output = table_output(node)
    if output is not None:
        return output.source_id, "group_sum"
    return None


def _closure(seeds: set[str], neighbors: dict[str, list[str]]) -> set[str]:
    seen = seeds.copy()
    pending = list(seeds)
    while pending:
        for neighbor in neighbors.get(pending.pop(), ()):
            if neighbor not in seen:
                seen.add(neighbor)
                pending.append(neighbor)
    return seen


def _inspect(document: SceneDocument, step: dict[str, str]) -> None:
    """Read the actual operands, configuration and destination before one write."""
    node_id, source_id = step["nodeId"], step["sourceId"]
    node = document.nodes[node_id]
    if step["kind"] == "note":
        state = document._plain_note(node_id).state
        if step["operation"] not in ("copy", "word_count"):
            raise SceneError("Choose Copy text or Word count for this input.")
        digest = _text_digest(document._text_output(source_id))
        last_digest = state.text_input_last_source_sha256
        step["configHash"] = _fingerprint([node.kind, step["operation"]])
        step["targetHash"] = _fingerprint([node.content, last_digest])
    else:
        _, output = document._require_table_output(node_id)
        digest = document._read_table(source_id).fingerprint(output.label_column, output.value_column)
        last_digest = output.current.source_hash
        step["configHash"] = _fingerprint([node.kind, step["operation"], output.label_column, output.value_column])
        step["targetHash"] = _fingerprint([output.current.version, last_digest, _current_result(node)])
        # Leave edited, up-to-date reports intact. A stale projection needs
        # permission to replace its result under the normal table output rules.
        if digest != last_digest:
            document._editable_table_output(node_id)
    step["inputHash"] = digest
    if digest == last_digest:
        step.update(status="current", detail="Input is up to date.")
    else:
        step.update(status="ready", detail="Input has changed." if last_digest else "Input has not run yet.")


def _evaluate(document: SceneDocument, step: dict[str, str]) -> None:
    if step["kind"] == "note":
        document.run_note_text_input(step["nodeId"])
    else:
        document.run_table_output(step["nodeId"])


def plan_graph_run(document: SceneDocument, selected_ids: list[str]) -> list[dict[str, str]]:
    """Preview selected branches and their prerequisites, or all bindings when empty.

    Simulate each ready operation on a private destination copy so downstream
    steps see precisely the result they would read during the real run.
    """
    bindings = {
        node.id: binding for node in document.nodes.values() if (binding := _binding(node)) is not None
    }
    children: dict[str, list[str]] = defaultdict(list)
    for target_id, (source_id, _) in bindings.items():
        children[source_id].append(target_id)
    included = set(bindings)
    if selected_ids:
        if any(node_id not in document.nodes for node_id in selected_ids):
            raise SceneError("A selected node no longer exists. Select the branch again.")
        branch = _closure(set(selected_ids), children)
        parents = {target: [source] for target, (source, _) in bindings.items()}
        included &= _closure(branch, parents)

    projected = copy.copy(document)
    projected.nodes = document.nodes.copy()
    steps = {}
    for target_id, (source_id, operation) in bindings.items():
        if target_id in included:
            node = document.nodes[target_id]
            steps[target_id] = {
                "nodeId": target_id, "sourceId": source_id, "kind": _kind(node), "operation": operation,
                "label": node.title or node.kind.title(), "status": "blocked", "detail": "",
                "inputHash": "", "configHash": "", "targetHash": "",
            }

    pending = deque(target for target, step in steps.items() if step["sourceId"] not in included)
    plan = []
    while pending:
        target = pending.popleft()
        step = steps[target]
        parent = steps.get(step["sourceId"])
        if parent is not None and parent["status"] == "blocked":
            step["detail"] = "The upstream step is blocked."
        else:
            try:
                _inspect(projected, step)
                if step["status"] == "ready":
                    projected.nodes[target] = copy.deepcopy(projected.nodes[target])
                    _evaluate(projected, step)
            except SceneError as exc:
                step.update(status="blocked", detail=str(exc))
        plan.append(step)
        pending.extend(child for child in children.get(target, ()) if child in included)

    for step in steps.values():
        if not step["detail"]:
            step["detail"] = "A dependency cycle prevents this step from running."
            plan.append(step)
    return plan


def _matching_node(document: SceneDocument, step: dict[str, str]) -> SceneNode:
    node = document.nodes.get(step["nodeId"])
    if node is None or _kind(node) != step["kind"] or _binding(node) != (step["sourceId"], step["operation"]):
        raise SceneError("The connection changed since the preview. Preview the branch again.")
    return node


def _output_digest(node: SceneNode) -> str:
    value = node.content if node.kind == "note" else _current_result(node)
    return _text_digest(value) if isinstance(value, str) else _fingerprint(value)


def validate_graph_run_ancestors(
    document: SceneDocument, step: dict[str, str], by_node_id: dict[str, dict[str, str]],
) -> None:
    """Reject results whose underlying inputs changed after they were checked or committed."""
    source_id = step["sourceId"]
    seen = {step["nodeId"]}
    while source_id in by_node_id:
        ancestor = by_node_id[source_id]
        if source_id in seen or ancestor["status"] not in ("current", "completed"):
            raise SceneError("The upstream step has not completed.")
        seen.add(source_id)
        node = _matching_node(document, ancestor)
        live = ancestor.copy()
        _inspect(document, live)
        if live["status"] != "current" or any(live[key] != ancestor[key] for key in ("inputHash", "configHash")):
            raise SceneError("An upstream input or configuration changed. Preview the branch again.")
        if ancestor["status"] == "current":
            output_changed = live["targetHash"] != ancestor["targetHash"]
        else:
            output_changed = _output_digest(node) != ancestor.get("outputHash")
        if output_changed:
            raise SceneError("An upstream output changed. Preview the branch again.")
        source_id = ancestor["sourceId"]
    source = document.nodes.get(source_id)
    if source is not None and _binding(source) is not None:
        raise SceneError("An upstream connection changed. Preview the branch again.")


def execute_graph_step(document: SceneDocument, step: dict[str, str]) -> str:
    """Apply one approved ready step and return the committed output digest."""
    if step["status"] not in ("ready", "pending"):
        raise SceneError("This step is not ready to run.")
    node = _matching_node(document, step)
    live = step.copy()
    _inspect(document, live)
    for key, name in (("inputHash", "input"), ("configHash", "configuration"), ("targetHash", "output")):
        if live[key] != step[key]:
            raise SceneError(f"The {name} changed since the preview. Preview the branch again.")
    _evaluate(document, step)
    return _output_digest(node)
