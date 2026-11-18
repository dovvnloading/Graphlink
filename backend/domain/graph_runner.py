"""Preview and execute the canvas's explicit data bindings in dependency order."""

from __future__ import annotations

import asyncio
import copy
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from backend.domain.model import SceneError, SceneNode
from backend.domain.node_states import ModelState, NoteState
from backend.domain.graph_model_binding import validate_local_model_identity
from backend.domain.nodes_content import _text_digest
from backend.domain.table_workflows import _current_result, _fingerprint, table_output
from graphlink_plugins.scientific.graph_run_adapter import (
    configured as science_configured, config_hash as science_config_hash,
    dependencies as science_dependencies, output_hash as science_output_hash,
    prepare as prepare_science,
)

if TYPE_CHECKING:
    from backend.domain.graph import SceneDocument


def _kind(node: SceneNode) -> str:
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


def _closure(seeds: set[str], neighbors: dict[str, list[str]]) -> set[str]:
    seen = seeds.copy()
    pending = list(seeds)
    while pending:
        for neighbor in neighbors.get(pending.pop(), ()):
            if neighbor not in seen:
                seen.add(neighbor)
                pending.append(neighbor)
    return seen


def _config_and_target(document: SceneDocument, step: dict[str, str]) -> None:
    """Pin output and configuration without requiring a future model reply."""
    node = document.nodes[step["nodeId"]]
    if step["kind"] == "scientific":
        step["configHash"] = science_config_hash(node)
        step["targetHash"] = science_output_hash(node)
    elif step["kind"] == "model":
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
    elif step["kind"] == "note":
        state = document._plain_note(step["nodeId"]).state
        if step["operation"] not in ("copy", "word_count"):
            raise SceneError("Choose Copy text or Word count for this input.")
        step["configHash"] = _fingerprint([node.kind, step["operation"]])
        step["targetHash"] = _fingerprint([node.content, state.text_input_last_source_sha256])
    else:
        _, output = document._require_table_output(step["nodeId"])
        step["configHash"] = _fingerprint([
            node.kind, step["operation"], output.label_column, output.value_column,
        ])
        step["targetHash"] = _fingerprint([output.current.version, output.current.source_hash, _current_result(node)])


def _inspect(document: SceneDocument, step: dict[str, str]) -> None:
    """Read the actual operands, configuration and destination before one write."""
    node_id, source_id = step["nodeId"], step["sourceId"]
    node = document.nodes[node_id]
    _config_and_target(document, step)
    if step["kind"] == "scientific":
        _, _, _, digest, same = prepare_science(document, node, lambda _pack: True)
        step["inputHash"] = digest
        step.update(
            status="current" if same else "ready",
            detail="Input is up to date." if same else "Scientific inputs are ready.",
        )
        return
    if step["kind"] == "model":
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
    elif step["kind"] == "note":
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
    else:
        _, output = document._require_table_output(node_id)
        digest = document._read_table(source_id).fingerprint(output.label_column, output.value_column)
        last_digest = output.current.source_hash
        # Leave edited, up-to-date reports intact. A stale projection needs
        # permission to replace its result under the normal table output rules.
        if digest != last_digest:
            document._editable_table_output(node_id)
    step["inputHash"] = digest
    if digest == last_digest:
        step.update(status="current", detail="Input is up to date.")
    else:
        step.update(status="ready", detail="Input has changed." if last_digest else "Input has not run yet.")
    if isinstance(node.state, ModelState):
        # Preview exposes the actual selected provider/model, without ever
        # revealing the prompt or guessing its result before dispatch.
        step["detail"] = (
            f"Local Ollama · {node.state.model_binding.model_id}. {step['detail']} "
            "Verify custom model aliases are not cloud-routed."
        )


def _evaluate(document: SceneDocument, step: dict[str, str]) -> None:
    if step["kind"] == "note":
        document.run_note_text_input(step["nodeId"])
    elif step["kind"] in {"model", "scientific"}:
        raise SceneError("This step requires its explicit asynchronous execution adapter.")
    else:
        document.run_table_output(step["nodeId"])


def step_sources(step: dict[str, Any]) -> tuple[str, ...]:
    """Every prerequisite, including a scientific operation's typed fan-in."""
    return tuple(step.get("sourceIds", (step["sourceId"],)))


def plan_graph_run(document: SceneDocument, selected_ids: list[str]) -> list[dict[str, Any]]:
    """Preview explicit bindings in stable dependency order, without calculation.

    Deterministic note/table projections run only on private node copies.
    Scientific and Model outputs are never speculatively calculated.
    """
    bindings: dict[str, tuple[str, str]] = {}
    sources: dict[str, tuple[str, ...]] = {}
    for node in document.nodes.values():
        if science_configured(node):
            parents = science_dependencies(document, node)
            operation = node.state.data["runner"].get("operationId", "")
            bindings[node.id] = (parents[0] if parents else "", operation)
            sources[node.id] = parents
        else:
            binding = _binding(node)
            if binding is not None:
                bindings[node.id] = binding
                sources[node.id] = (binding[0],)

    children: dict[str, list[str]] = defaultdict(list)
    for target_id, parents in sources.items():
        for parent_id in parents:
            children[parent_id].append(target_id)
    included = set(bindings)
    if selected_ids:
        if any(node_id not in document.nodes for node_id in selected_ids):
            raise SceneError("A selected node no longer exists. Select the branch again.")
        branch = _closure(set(selected_ids), children)
        included &= _closure(branch, {target: list(parents) for target, parents in sources.items()})

    projected = copy.copy(document)
    projected.nodes = document.nodes.copy()
    steps: dict[str, dict[str, Any]] = {}
    for target_id, (source_id, operation) in bindings.items():
        if target_id in included:
            node = document.nodes[target_id]
            step = {
                "nodeId": target_id, "sourceId": source_id, "kind": _kind(node),
                "operation": operation, "label": node.title or node.kind.title(),
                "status": "blocked", "detail": "", "inputHash": "",
                "configHash": "", "targetHash": "",
            }
            if science_configured(node):
                step["sourceIds"] = list(sources[target_id])
            steps[target_id] = step

    remaining = {
        target_id: sum(parent in steps for parent in sources[target_id])
        for target_id in steps
    }
    pending = deque(target for target, count in remaining.items() if not count)
    plan = []
    while pending:
        target = pending.popleft()
        step = steps[target]
        parents = [steps[parent] for parent in sources[target] if parent in steps]
        if any(parent["status"] == "blocked" for parent in parents):
            step["detail"] = "The upstream step is blocked."
        elif any(
            parent["status"] == "waiting"
            or (parent["kind"] in {"model", "scientific"} and parent["status"] == "ready")
            for parent in parents
        ):
            # Neither a Model reply nor an isolated scientific calculation
            # can be guessed during a read-only preview.
            try:
                _config_and_target(projected, step)
                step.update(status="waiting", detail="Awaiting an upstream result.")
            except SceneError as exc:
                step.update(status="blocked", detail=str(exc))
        else:
            try:
                _inspect(projected, step)
                if step["status"] == "ready" and step["kind"] not in {"model", "scientific"}:
                    projected.nodes[target] = copy.deepcopy(projected.nodes[target])
                    _evaluate(projected, step)
            except SceneError as exc:
                step.update(status="blocked", detail=str(exc))
        plan.append(step)
        for child in children.get(target, ()):
            if child in remaining:
                remaining[child] -= 1
                if remaining[child] == 0:
                    pending.append(child)

    for target, step in steps.items():
        if remaining[target] > 0:
            step.update(status="blocked", detail="A dependency cycle prevents this step from running.")
            plan.append(step)
    return plan

def _matching_node(document: SceneDocument, step: dict[str, Any]) -> SceneNode:
    node = document.nodes.get(step["nodeId"])
    if node is None or _kind(node) != step["kind"]:
        raise SceneError("The connection changed since the preview. Preview the branch again.")
    if step["kind"] == "scientific":
        if (not science_configured(node)
                or node.state.data["runner"].get("operationId") != step["operation"]
                or science_dependencies(document, node) != step_sources(step)):
            raise SceneError("The scientific inputs changed since preview. Preview the branch again.")
    elif _binding(node) != (step["sourceId"], step["operation"]):
        raise SceneError("The connection changed since the preview. Preview the branch again.")
    return node


def _output_digest(node: SceneNode) -> str:
    if science_configured(node):
        return science_output_hash(node)
    value = node.content if node.kind in {"note", "model"} else _current_result(node)
    return _text_digest(value) if isinstance(value, str) else _fingerprint(value)


def validate_graph_run_ancestors(
    document: SceneDocument, step: dict[str, Any], by_node_id: dict[str, dict[str, Any]],
) -> None:
    """Verify *all* source branches, pins and outputs before a dependent write."""
    visiting: set[str] = {step["nodeId"]}
    verified: set[str] = set()

    def check(source_id: str) -> None:
        if source_id in visiting:
            raise SceneError("The upstream dependency graph has a cycle.")
        if source_id in verified:
            return
        ancestor = by_node_id.get(source_id)
        if ancestor is None:
            source = document.nodes.get(source_id)
            if source is not None and (_binding(source) is not None or science_configured(source)):
                raise SceneError("An upstream connection changed. Preview the branch again.")
            return
        if ancestor["status"] not in {"current", "completed"}:
            raise SceneError("The upstream step has not completed.")
        visiting.add(source_id)
        node = _matching_node(document, ancestor)
        live = ancestor.copy()
        _inspect(document, live)
        if live["status"] != "current" or any(
            live[key] != ancestor[key] for key in ("inputHash", "configHash")
        ):
            raise SceneError("An upstream input or configuration changed. Preview the branch again.")
        if ancestor["status"] == "current":
            changed = live["targetHash"] != ancestor["targetHash"]
        else:
            changed = _output_digest(node) != ancestor.get("outputHash")
        if changed:
            raise SceneError("An upstream output changed. Preview the branch again.")
        for parent_id in step_sources(ancestor):
            if parent_id:
                check(parent_id)
        visiting.remove(source_id)
        verified.add(source_id)

    for source_id in step_sources(step):
        if source_id:
            check(source_id)

def execute_graph_step(document: SceneDocument, step: dict[str, str]) -> str:
    """Apply one approved ready step and return the committed output digest."""
    if step["status"] not in ("ready", "pending"):
        raise SceneError("This step is not ready to run.")
    if step["kind"] == "scientific":
        raise SceneError("Scientific steps require their bounded asynchronous worker.")
    node = _matching_node(document, step)
    live = step.copy()
    _inspect(document, live)
    for key, name in (("inputHash", "input"), ("configHash", "configuration"), ("targetHash", "output")):
        if live[key] != step[key]:
            raise SceneError(f"The {name} changed since the preview. Preview the branch again.")
    _evaluate(document, step)
    return _output_digest(node)


async def schedule_graph_steps(
    steps: list[dict[str, Any]],
    *,
    max_concurrency: int,
    execute: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    checkpoint_blocked: Callable[[], Awaitable[None]],
    dependencies: Callable[[dict[str, Any]], tuple[str, ...]] | None = None,
) -> None:
    """Run eligible steps in stable plan order, without waiting for unrelated branches.

    The current graph bindings each have one source. The optional dependency
    selector also supports multiple prerequisites (fan-in) for callers with
    multi-input steps. No dependent is scheduled until *all* its prerequisites
    have completed or were already current. A failed or uncertain branch only
    blocks its descendants.

    Only the caller writes graph outputs or checkpoints. In particular, its
    checkpoint callback must serialize concurrent workers' SQLite saves.
    """
    if type(max_concurrency) is not int or not 1 <= max_concurrency <= 8:
        raise ValueError("Graph Runner concurrency must be between 1 and 8.")

    by_node_id = {step["nodeId"]: step for step in steps}
    successful = {"current", "completed"}
    unsuccessful = {"blocked", "failed", "cancelled", "reconciliation-required"}

    def parents(step: dict[str, Any]) -> list[dict[str, Any]]:
        sources = dependencies(step) if dependencies is not None else (step["sourceId"],)
        # Deduplicate shared inputs without changing their plan order.
        return [by_node_id[source] for source in dict.fromkeys(sources) if source in by_node_id]

    active: dict[asyncio.Task[None], int] = {}
    try:
        while True:
            in_flight = set(active.values())
            waiting = [
                (index, step) for index, step in enumerate(steps)
                if step["status"] == "pending" and index not in in_flight
            ]

            blocked = [step for _, step in waiting if any(
                parent["status"] in unsuccessful for parent in parents(step)
            )]
            if blocked:
                for step in blocked:
                    step.update(
                        status="blocked", detail="The upstream step is blocked or incomplete.",
                        finishedAt=datetime.now(timezone.utc).isoformat(),
                    )
                await checkpoint_blocked()
                continue

            ready = [(index, step) for index, step in waiting if all(
                parent["status"] in successful for parent in parents(step)
            )]
            for index, step in ready[:max(0, max_concurrency - len(active))]:
                active[asyncio.create_task(execute(step))] = index

            if active:
                done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    active.pop(task)
                    task.result()  # Propagate persistence failures; never continue dispatching.
                continue

            if not waiting:
                return

            # A malformed/cyclic recovered plan cannot make progress. Mark
            # only the affected pending steps; completed branches stay intact.
            for _, step in waiting:
                step.update(
                    status="blocked", detail="A dependency cycle prevents this step from running.",
                    finishedAt=datetime.now(timezone.utc).isoformat(),
                )
            await checkpoint_blocked()
            return
    finally:
        # The parent intent owns the graph mutation guard. Never release it
        # while a child is still dispatching, applying a result, or saving.
        if active:
            for task in active:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)
