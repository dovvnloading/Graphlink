"""Preview and execute the canvas's explicit data bindings in dependency order."""

from __future__ import annotations

import asyncio
import copy
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from backend.domain.model import SceneError, SceneNode
from backend.domain.node_states import NoteState
from backend.workflow import canvas as reusable
from backend.workflow.graph_steps import (
    _binding, _config_and_target, _evaluate, _inspect, _kind, _matching_node, _output_digest,
    adapter_for_step, discover_binding, mcp_configured, mcp_dependencies,
    science_configured, science_dependencies, step_sources as step_sources,
)

if TYPE_CHECKING:
    from backend.domain.graph import SceneDocument


def is_bound_node(node: SceneNode) -> bool:
    """Only explicit data bindings, never executable agent/plugin actions.

    Note model bindings use Graph Runner's existing dedicated Model preview,
    not its ordinary deterministic graph-plan endpoint.
    """
    return (
        node.kind == reusable.KIND or science_configured(node) or mcp_configured(node)
        or _binding(node) is not None
        or isinstance(node.state, NoteState) and node.state.model_text_input is not None
    )


def bound_dependents(document: SceneDocument, source_ids: set[str]) -> list[str]:
    """Find typed bound outputs downstream of sources, without running them.

    Structural ancestry does not capture text ports or Model inputs. Model,
    note, table and configured scientific dependencies are separate from
    visual edges, and can therefore feed an output below a changed message
    without appearing in a canonical chat branch traversal.
    """
    children: dict[str, list[str]] = defaultdict(list)
    for node in document.nodes.values():
        if node.kind == reusable.KIND:
            try:
                parents = reusable.dependencies(node)
            except SceneError:
                parents = ()
        elif mcp_configured(node):
            try:
                parents = mcp_dependencies(document, node)
            except SceneError:
                parents = ()
        elif science_configured(node):
            try:
                parents = science_dependencies(document, node)
            except SceneError:
                # A broken configuration cannot be safely resolved into a
                # source; Graph Runner will report that on manual preview.
                parents = ()
        elif isinstance(node.state, NoteState) and node.state.model_text_input is not None:
            parents = (node.state.model_text_input.source_id,)
        else:
            binding = _binding(node)
            parents = (binding[0],) if binding else ()
        for parent in parents:
            if parent:
                children[parent].append(node.id)
    visited = set(source_ids)
    pending = deque(source_ids)
    result: list[str] = []
    while pending:
        for child in children.get(pending.popleft(), ()):
            if child not in visited:
                visited.add(child)
                result.append(child)
                pending.append(child)
    return result


def _closure(seeds: set[str], neighbors: dict[str, list[str]]) -> set[str]:
    seen = seeds.copy()
    pending = list(seeds)
    while pending:
        for neighbor in neighbors.get(pending.pop(), ()):
            if neighbor not in seen:
                seen.add(neighbor)
                pending.append(neighbor)
    return seen


def plan_graph_run(document: SceneDocument, selected_ids: list[str]) -> list[dict[str, Any]]:
    """Preview explicit bindings in stable dependency order, without calculation.

    Deterministic note/table projections run only on private node copies.
    Scientific and Model outputs are never speculatively calculated.
    """
    bindings: dict[str, tuple[str, str]] = {}
    sources: dict[str, tuple[str, ...]] = {}
    invalid_bindings: dict[str, str] = {}
    for node in document.nodes.values():
        binding = discover_binding(document, node)
        if binding is not None:
            bindings[node.id] = (binding.source_id, binding.operation)
            sources[node.id] = binding.sources
            if binding.error:
                invalid_bindings[node.id] = binding.error

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
            step: dict[str, Any] = {
                "nodeId": target_id, "sourceId": source_id, "kind": _kind(node),
                "operation": operation, "label": node.title or node.kind.title(),
                "status": "blocked", "detail": "", "inputHash": "",
                "configHash": "", "targetHash": "",
            }
            if adapter_for_step(step).fan_in:
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
        parent_steps = [steps[parent] for parent in sources[target] if parent in steps]
        if target in invalid_bindings:
            step["detail"] = invalid_bindings[target]
        elif any(parent["status"] == "blocked" for parent in parent_steps):
            step["detail"] = "The upstream step is blocked."
        elif any(
            parent["status"] == "waiting"
            or (not adapter_for_step(parent).predictable and parent["status"] == "ready")
            for parent in parent_steps
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
                if step["status"] == "ready" and adapter_for_step(step).predictable:
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
            if source is not None and (_binding(source) is not None or science_configured(source)
                                       or mcp_configured(source) or source.kind == reusable.KIND):
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
    if not adapter_for_step(step).sync_entry:
        raise SceneError("This step requires its explicit asynchronous execution adapter.")
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
