"""PlanningOps - the SceneDocument methods for the plan and harness node
kinds.

A MIXIN, composed exactly once, by backend/domain/graph.py's
SceneDocument. Method bodies are relocated VERBATIM from graph.py;
only the class wrapper, its docstring and the imports are new, and the
methods are regrouped by kind rather than left in the order successive
increments happened to append them in.

See backend/domain/nodes_code_review.py's docstring for why the
per-kind method groups are being lifted out of SceneDocument at all.
"""

from __future__ import annotations

import uuid
from typing import Any

from backend.domain._composed import SceneDocumentParts
from backend.domain.model import CHAT_TITLE_PREVIEW_LENGTH, SceneError, SceneNode
from backend.domain.node_states import HarnessState, PlanState


class PlanningOps(SceneDocumentParts):
    """The two nodes that hold an agent's plan of work: the Builder's plan
    checklist and the workspace harness.

    Both are orchestration scaffolding rather than content - a plan node
    carries steps with a status apiece, a harness node carries the caps a
    run must stay inside - so neither has the parent-required posture the
    content kinds share.
    """

    # -- ADR-008 stage 8.3: plan node (the Builder's checklist) --------------

    _PLAN_STEP_STATUSES = ("pending", "running", "done", "failed", "skipped")

    def add_plan_node(
        self,
        x: float,
        y: float,
        goal: str,
        *,
        mode: str = "copilot",
        max_steps: int = 12,
        max_tokens: int = 150_000,
        max_wall_seconds: int = 900,
    ) -> SceneNode:
        """The Builder plan node's creation primitive. Free-floating like a
        note (a build STARTS from a goal, it does not continue an existing
        branch - the nodes the build creates are the ones that connect);
        `content` reuses the goal text the same way web_research reuses
        content for its query. Everything else lives on PlanState - see its
        own docstring for the state machine and the plan-node-as-resume-
        point contract."""
        if mode not in ("copilot", "autopilot"):
            raise SceneError(f"unknown builder mode: {mode}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title=f"Build: {str(goal)[:CHAT_TITLE_PREVIEW_LENGTH]}" if goal else "Build",
            kind="plan",
            content=str(goal),
            state=PlanState(
                plan_goal=str(goal),
                builder_mode=mode,
                builder_max_steps=int(max_steps),
                builder_max_tokens=int(max_tokens),
                builder_max_wall_seconds=int(max_wall_seconds),
            ),
        )
        self.nodes[node_id] = node
        return node

    def set_plan_steps(self, node_id: str, steps: list) -> SceneNode:
        """Replaces the plan's step list - the one plan mutator that goes
        through record_command (a user editing the checklist, or the
        model's replan tool): step CONTENT is document state a Ctrl+Z must
        reach, unlike the run-lifecycle fields (builder_status/spent_*/
        awaiting_*) which the loop writes directly, exactly as Execution
        Sandbox's own run pipeline writes its awaiting/progress fields.

        Steps whose status is not "pending" are immutable history - a
        replacement must carry every non-pending step through unchanged
        (same id, title, status), enforced here so neither a user edit nor
        a model replan can rewrite what already happened."""
        node = self.nodes.get(node_id)
        if node is None or not isinstance(node.state, PlanState):
            raise SceneError(f"not a plan node: {node_id}")
        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for raw in steps:
            if not isinstance(raw, dict):
                raise SceneError("each step must be an object")
            step_id = str(raw.get("id") or f"s{len(normalized) + 1}")
            if step_id in seen_ids:
                raise SceneError(f"duplicate step id: {step_id}")
            seen_ids.add(step_id)
            status = str(raw.get("status") or "pending")
            if status not in self._PLAN_STEP_STATUSES:
                raise SceneError(f"unknown step status: {status}")
            title = str(raw.get("title") or "").strip()
            if not title:
                raise SceneError("each step needs a title")
            normalized.append({
                "id": step_id, "title": title, "status": status,
                "detail": str(raw.get("detail") or ""),
            })
        frozen = {s["id"]: s for s in node.state.plan_steps if s.get("status") != "pending"}
        for step_id, original in frozen.items():
            replacement = next((s for s in normalized if s["id"] == step_id), None)
            if replacement is None:
                raise SceneError(
                    f"step {step_id!r} has already run ({original['status']}) and cannot be removed"
                )
            if replacement["title"] != original["title"] or replacement["status"] != original["status"]:
                raise SceneError(
                    f"step {step_id!r} has already run ({original['status']}) and cannot be rewritten"
                )
        node.state.plan_steps = normalized
        return node

    # -- PLAN-2026-08-24 H1: harness node (the workspace agent) --------------

    def add_harness_node(self, x: float, y: float, goal: str, *, max_turns: int = 16) -> SceneNode:
        """The harness node's creation primitive. Free-floating like a plan
        node (a task starts from a prompt, it does not continue an existing
        branch); harness_workspace_id is minted here, ONCE - the same
        code_sandbox_sandbox_id precedent, see HarnessState's own docstring
        for why node.id is not durable enough to name the on-disk
        workspace."""
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title=f"Agent: {str(goal)[:CHAT_TITLE_PREVIEW_LENGTH]}" if goal else "Agent",
            kind="harness",
            content=str(goal),
            state=HarnessState(
                harness_goal=str(goal),
                harness_workspace_id=uuid.uuid4().hex[:12],
                harness_max_turns=int(max_turns),
            ),
        )
        self.nodes[node_id] = node
        return node
