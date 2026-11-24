"""Detached version-pinned workflow scheduler; no active canvas mutations."""
from __future__ import annotations

import asyncio
import copy
import json
import math
import secrets
import time
import uuid
from decimal import Decimal
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from backend.asset_store import store_for
from backend.canvas import SceneDocument
from .adapter import StepContext
from .artifacts import WorkflowArtifacts, canonical
from .contracts import JsonValue, PortValue, StepOutputSource, WorkflowInputSource, WorkflowSpec, WorkflowStepSpec
from .control_flow import APPROVAL, BRANCHES, MAP, MERGE, ControlBlocked
from .detached_store import DetachedRunStore, utc_now
from .planning import Plan, PolicyResolver, RunPreview, build_plan, default_policy
from .ports import coerce_port
from .registry import StepAdapterRegistry, builtin_registry


@dataclass(frozen=True, slots=True)
class RunLimits:
    cloud_concurrency: int = 4
    local_concurrency: int = 1
    cost_usd: float = 2.00
    wall_time_seconds: float = 30 * 60
    max_steps: int = 500

    def __post_init__(self):
        for value in (self.cloud_concurrency, self.local_concurrency, self.max_steps):
            if type(value) is not int or value < 1:
                raise ValueError("Workflow concurrency and step limits must be positive integers.")
        if (not math.isfinite(self.cost_usd) or self.cost_usd < 0
                or not math.isfinite(self.wall_time_seconds) or self.wall_time_seconds <= 0):
            raise ValueError("Workflow cost and duration limits must be finite and nonnegative.")


@dataclass
class _Attempt:
    step: WorkflowStepSpec
    context: StepContext
    task: asyncio.Task | None = None
    dispatched: bool = False
    cost: float | None = None
    completed_order: int = 0


@dataclass
class _Run:
    workspace_id: int
    plan: Plan
    record: dict[str, Any]
    scratch: SceneDocument = field(default_factory=SceneDocument)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    stop_reason: str | None = None
    stop_status: str = "cancelled"
    task: asyncio.Task | None = None
    approvals: dict[str, asyncio.Future] = field(default_factory=dict)
    completion_counter: int = 0
    parent: _Run | None = None
    map_step: str | None = None
    item_index: int | None = None

    def stop(self, reason: str, status: str = "cancelled") -> None:
        if self.stop_reason is None:
            self.stop_reason = reason
            self.stop_status = status
        self.wake.set()


def _event(record: dict[str, Any], kind: str, *, step_id: str | None = None,
           message: str = "", data: Any = None) -> None:
    record["events"].append({
        "schema_version": 1, "run_id": record["run_id"], "sequence": len(record["events"]),
        "timestamp": utc_now(), "kind": kind, "step_id": step_id,
        "attempt": 1 if step_id is not None else None, "message": message, "data": data,
    })


def _new_record(plan: Plan, limits: RunLimits) -> dict[str, Any]:
    run_id = uuid.uuid4().hex
    record = {
        "schema_version": 1, "run_id": run_id, "spec": asdict(plan.spec),
        "spec_fingerprint": plan.preview.workflow_hash, "status": "pending",
        "created_at": utc_now(), "started_at": None, "finished_at": None,
        "inputs": plan.inputs, "outputs": {}, "error": None,
        "preview": asdict(plan.preview), "limits": asdict(limits),
        "cost_usd": 0.0, "steps": {}, "events": [],
    }
    for step in plan.spec.steps:
        record["steps"][step.step_id] = {
            "schema_version": 1, "run_id": run_id, "step_id": step.step_id, "attempt": 1,
            "side_effect_class": step.adapter.side_effect_class, "status": "pending",
            "fingerprint": None, "started_at": None, "finished_at": None, "outputs": {},
            "error": None, "cost_usd": 0.0,
        }
    _event(record, "run-created")
    return record


def _source(run: _Run, source: WorkflowInputSource | StepOutputSource) -> dict[str, Any] | None:
    if isinstance(source, WorkflowInputSource):
        return run.plan.inputs.get(source.port)
    return run.record["steps"][source.step_id]["outputs"].get(source.port)


def _observe(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


class WorkflowEngine:
    """One engine per backend, shared by workspace sessions on its event loop."""

    def __init__(self, db_path: Path, *, registry: StepAdapterRegistry | None = None,
                 limits: RunLimits | None = None, policy_for: PolicyResolver | None = None,
                 recover: bool = True):
        self.store = DetachedRunStore(db_path)
        self.artifacts = WorkflowArtifacts(store_for(Path(db_path)))
        if registry is None:
            from .scratch_adapters import detached_registry
            registry = detached_registry(builtin_registry())
        self.registry = registry.with_entries(())
        self.limits = limits or RunLimits()
        def adapter_policy(spec):
            adapter = self.registry.resolve(spec.adapter.adapter_id, spec.adapter.adapter_version)
            resolve = getattr(adapter, "execution_policy", None)
            return resolve(spec) if resolve is not None else default_policy(spec)

        self.policy_for = policy_for or adapter_policy
        self._lanes = {
            "cloud": asyncio.Semaphore(self.limits.cloud_concurrency),
            "local": asyncio.Semaphore(self.limits.local_concurrency),
        }
        self._runs: dict[str, _Run] = {}
        self._previews: dict[str, tuple[RunPreview, float]] = {}
        self._approvals: dict[str, tuple[str, int, float]] = {}
        if recover:
            self.store.interrupt_unfinished()

    def _expire_approvals(self) -> None:
        now = time.monotonic()
        self._previews = {key: entry for key, entry in self._previews.items() if entry[1] > now}
        self._approvals = {key: entry for key, entry in self._approvals.items() if entry[2] > now}

    def preview(self, spec: WorkflowSpec, parameters: Mapping[str, PortValue]) -> RunPreview:
        plan = build_plan(spec, parameters, self.registry, self.artifacts, self.policy_for)
        self._expire_approvals()
        self._previews[plan.preview.fingerprint] = (copy.deepcopy(plan.preview), time.monotonic() + 900)
        return plan.preview

    def approve(self, workspace_id: int, preview: RunPreview) -> str:
        """Trusted host consent boundary. Call only after explicit user review."""
        self._expire_approvals()
        issued = self._previews.get(preview.fingerprint)
        if issued is None or issued[0] != preview:
            raise ValueError("Preview the workflow again before approving it.")
        token = secrets.token_urlsafe(32)
        self._approvals[token] = (preview.fingerprint, workspace_id, time.monotonic() + 900)
        return token

    def approve_fingerprint(self, workspace_id: int, fingerprint: str) -> str:
        """Approve an issued preview by its exact fingerprint."""
        self._expire_approvals()
        issued = self._previews.get(fingerprint)
        if issued is None:
            raise ValueError("Preview the workflow again before approving it.")
        return self.approve(workspace_id, issued[0])

    def start(self, workspace_id: int, spec: WorkflowSpec, parameters: Mapping[str, PortValue],
              *, approval_token: str | None = None) -> str:
        """Admit a frozen run, then schedule it; caller receives its cancel ID."""
        loop = asyncio.get_running_loop()
        spec = copy.deepcopy(spec)
        parameters = copy.deepcopy(dict(parameters))
        plan = build_plan(spec, parameters, self.registry, self.artifacts, self.policy_for)
        self._expire_approvals()
        approved_fingerprint = plan.preview.fingerprint
        if plan.preview.requires_approval or approval_token is not None:
            entry = self._approvals.pop(approval_token, None)
            if entry is None or entry[:2] != (approved_fingerprint, workspace_id):
                raise PermissionError("Approve this exact workflow preview before running it.")
        plan = build_plan(spec, parameters, self.registry, self.artifacts, self.policy_for, persist=True)
        if approved_fingerprint != plan.preview.fingerprint:
            raise PermissionError("Workflow preview changed; approve it again.")
        record = _new_record(plan, self.limits)
        run = _Run(workspace_id, plan, record)
        self.store.create(workspace_id, record)
        run_id = record["run_id"]
        self._runs[run_id] = run
        run.task = loop.create_task(self._drive(run))
        run.task.add_done_callback(_observe)
        return run_id

    async def wait(self, workspace_id: int, run_id: str) -> dict[str, Any] | None:
        run = self._runs.get(run_id)
        if run is not None and run.workspace_id == workspace_id:
            return json.loads(canonical(await asyncio.shield(run.task)))
        return self.store.read(workspace_id, run_id)

    def cancel(self, workspace_id: int, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.workspace_id != workspace_id or run.stop_reason is not None:
            return False
        run.stop("Cancelled by user.")
        return True

    def pending_approvals(self) -> list[dict[str, Any]]:
        """Active gates, scoped to the authenticated local application."""
        return [
            {"workspace_id": run.workspace_id, "run_id": run.record["run_id"],
             "workflow": run.plan.spec.name, "step_id": step_id,
             "message": run.record["steps"][step_id]["approval"]["message"]}
            for run in self._runs.values()
            for step_id, future in run.approvals.items()
            if not future.done() and run.stop_reason is None
        ]

    def decide_step(self, workspace_id: int, run_id: str, step_id: str, approved: bool) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.workspace_id != workspace_id or run.stop_reason is not None:
            return False
        future = run.approvals.get(step_id)
        if future is None or future.done() or type(approved) is not bool:
            return False
        future.set_result(approved)
        return True

    async def _wait_for_approval(self, run: _Run, step_id: str, message: str) -> None:
        future = asyncio.get_running_loop().create_future()
        run.approvals[step_id] = future
        step = run.record["steps"][step_id]
        step["approval"] = {"status": "pending", "message": message}
        _event(run.record, "step-progress", step_id=step_id, message=message,
               data={"approval": "pending"})
        self._checkpoint(run)
        try:
            approved = await future
            step["approval"]["status"] = "approved" if approved else "rejected"
            _event(run.record, "step-progress", step_id=step_id,
                   data={"approval": step["approval"]["status"]})
            self._checkpoint(run)
            if not approved:
                raise ControlBlocked("Approval was rejected.")
        finally:
            run.approvals.pop(step_id, None)

    async def shutdown(self) -> None:
        runs = list(self._runs.values())
        for run in runs:
            run.stop("The backend is shutting down.", "interrupted")
        await asyncio.gather(*(asyncio.shield(run.task) for run in runs), return_exceptions=True)

    def _checkpoint(self, run: _Run) -> None:
        if run.parent is not None:
            parent = run.parent
            step = parent.record["steps"][run.map_step]
            # A cancelled outer attempt must never mutate a terminal receipt.
            if parent.record["status"] == "running" and step["status"] == "running":
                step.setdefault("map_items", {})[str(run.item_index)] = copy.deepcopy({
                    key: run.record[key] for key in
                    ("run_id", "status", "steps", "outputs", "error", "cost_usd")
                })
                self._checkpoint(parent)
            return
        self.store.checkpoint(run.workspace_id, run.record)

    def _finish_step(self, run: _Run, step_id: str, status: str, error: str | None = None,
                     outputs: dict[str, Any] | None = None, cost: float = 0.0) -> None:
        step = run.record["steps"][step_id]
        step.update(status=status, finished_at=utc_now(), error=error, cost_usd=cost)
        if outputs is not None:
            step["outputs"] = outputs
            spec = next(s for s in run.plan.spec.steps if s.step_id == step_id)
            branches = BRANCHES.get(spec.adapter.adapter_id, ())
            skipped = [port.name for port in spec.adapter.ports.outputs
                       if port.name in branches and port.name not in outputs]
            if skipped:
                step["skipped_outputs"] = skipped
        if step.get("approval", {}).get("status") == "pending":
            step["approval"]["status"] = status
        for item in step.get("map_items", {}).values():
            if item["status"] in ("pending", "running") and status != "completed":
                item.update(status=status, error=error)
                for child in item["steps"].values():
                    if child["status"] in ("pending", "running"):
                        child.update(status=status, error=error, finished_at=utc_now())
        run.record["cost_usd"] = float(sum(Decimal(str(s["cost_usd"])) for s in run.record["steps"].values()))
        _event(run.record, f"step-{status}", step_id=step_id, message=error or "",
               data={"skipped_outputs": step["skipped_outputs"]} if "skipped_outputs" in step else None)
        self._checkpoint(run)

    def _inputs(self, run: _Run, spec: WorkflowStepSpec) -> dict[str, PortValue]:
        ports = {port.name: port for port in spec.adapter.ports.inputs}
        result = {}
        for binding in spec.bindings:
            value = _source(run, binding.source)
            if value is not None:
                result[binding.port] = coerce_port(self.artifacts.unpack(value), ports[binding.port])
        if spec.adapter.adapter_id == MERGE and json.loads(spec.config_json).get("mode", "all") == "first":
            order = {event["step_id"]: event["sequence"] for event in run.record["events"]
                     if event["kind"] == "step-completed"}
            available = [binding for binding in spec.bindings if binding.port in result]
            winner = min(available, key=lambda binding: (
                -1 if isinstance(binding.source, WorkflowInputSource) else order[binding.source.step_id]
            ))
            result = {winner.port: result[winner.port]}
        for name, port in ports.items():
            if port.required and name not in result:
                raise ValueError(f"Missing required input {name!r}.")
        return result

    def _outputs(self, spec: WorkflowStepSpec, values: Mapping[str, PortValue]) -> dict[str, Any]:
        ports = {port.name: port for port in spec.adapter.ports.outputs}
        if set(values) - ports.keys() or any(p.required and p.name not in values for p in ports.values()):
            raise ValueError("Adapter did not return its declared outputs.")
        result = {}
        for name, value in values.items():
            if value.kind != ports[name].kind:
                raise ValueError(f"Adapter returned the wrong kind for output {name!r}.")
            result[name] = self.artifacts.pack(value)
        return result

    def _launch(self, run: _Run, spec: WorkflowStepSpec) -> _Attempt:
        policy = run.plan.policies[spec.step_id]

        def report_cost(cost: float) -> None:
            if not math.isfinite(cost) or cost < (attempt.cost or 0):
                raise ValueError("Reported workflow cost must be finite and cumulative.")
            attempt.cost = cost
            if cost > policy.cost_usd:
                reason = "Run stopped: a step exceeded its approved cost estimate (cost limit)."
                run.stop(reason, "failed")
                if run.parent is not None:
                    run.parent.stop(reason, "failed")

        context = StepContext(
            run.record["run_id"], spec.step_id, 1, scratch_document=run.scratch,
            artifacts=self.artifacts, model_identity=policy.model_identity,
            cost_limit_usd=policy.cost_usd, report_cost=report_cost,
            wait_for_approval=(lambda message: self._wait_for_approval(run, spec.step_id, message))
            if spec.adapter.adapter_id == APPROVAL else None,
            run_map=(lambda config, items: self._run_map(run, attempt, config, items))
            if spec.adapter.adapter_id == MAP else None,
        )
        attempt = _Attempt(spec, context)
        async def execute():
            try:
                return await self._execute(run, attempt)
            finally:
                run.completion_counter += 1
                attempt.completed_order = run.completion_counter

        attempt.task = asyncio.create_task(execute())
        return attempt

    async def _execute(self, run: _Run, attempt: _Attempt) -> dict[str, Any]:
        spec = attempt.step
        step_id = spec.step_id
        policy = run.plan.policies[step_id]
        adapter = run.plan.adapters[step_id]
        # Orchestration must not hold a provider permit while waiting for child work or a human.
        lane = nullcontext() if spec.adapter.adapter_id in (MAP, APPROVAL) else self._lanes[policy.execution_lane]
        async with lane:
            if run.stop_reason is not None:
                raise asyncio.CancelledError
            prepared = adapter.prepare(spec, self._inputs(run, spec))
            fingerprint = adapter.fingerprint(prepared)
            # Persist dispatch before an effect; no replay after restart.
            run.record["steps"][step_id].update(
                status="running", started_at=utc_now(), fingerprint=fingerprint,
            )
            _event(run.record, "step-started", step_id=step_id)
            self._checkpoint(run)
            attempt.dispatched = True
            values = await adapter.run(prepared, attempt.context)
            if run.stop_reason is not None:
                raise asyncio.CancelledError
            return self._outputs(spec, values)

    async def _run_map(self, run: _Run, attempt: _Attempt, config: dict, items: list) -> list:
        step_id = attempt.step.step_id
        template = run.plan.map_plans[step_id]
        expected = run.plan.preview.control_flow[step_id]["item_count"]
        if len(items) != expected:
            raise ControlBlocked("Map item count changed from the approved preview.")
        results = [None] * len(items)
        # The children share the very same global local/cloud semaphores as
        # ordinary steps. Do not reserve a semaphore for the Map itself.
        lanes = {policy.execution_lane for policy in template.policies.values()}
        provider_limit = min(
            [self.limits.cloud_concurrency if lane == "cloud" else self.limits.local_concurrency
             for lane in lanes] or [self.limits.local_concurrency]
        )
        concurrency = min(config.get("concurrency", 1), provider_limit)
        on_error = config.get("on_error", "stop")

        async def execute_item(index):
            # Only the item changes. Child policy, model identity, spec and
            # adapter versions were pinned in the parent's approved preview.
            plan = build_plan(
                template.spec, {"item": JsonValue(items[index])}, self.registry, self.artifacts,
                lambda step: template.policies[step.step_id], persist=True,
            )
            child = _Run(run.workspace_id, plan, _new_record(plan, self.limits),
                         parent=run, map_step=step_id, item_index=index)
            record = await self._drive(child)
            outputs = {name: asdict(self.artifacts.unpack(value))
                       for name, value in record["outputs"].items()}
            return {"index": index, "status": record["status"], "outputs": outputs,
                    "error": record["error"], "cost_usd": record["cost_usd"]}

        # Batches cap in-flight items and never dispatch another batch after
        # a stop-mode error. Within a batch, cancel outstanding items on
        # the first observed failure, retaining already checkpointed receipts.
        for start in range(0, len(items), concurrency):
            active = {asyncio.create_task(execute_item(index)): index
                      for index in range(start, min(start + concurrency, len(items)))}
            try:
                while active:
                    done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                    for task in sorted(done, key=lambda task: active[task]):
                        index = active.pop(task)
                        try:
                            result = task.result()
                        except Exception:
                            result = {"index": index, "status": "failed", "outputs": {},
                                      "error": "Map item execution failed.", "cost_usd": 0.0}
                        results[index] = result
                        if result["status"] != "completed" and on_error == "stop":
                            raise ControlBlocked(f"Map item {index + 1} failed; stopped remaining items.")
            finally:
                for task in active:
                    task.cancel()
                if active:
                    await asyncio.gather(*active, return_exceptions=True)
                # Charge actual or conservatively reserved costs of dispatched
                # children, not the estimates of items never started.
                receipts = run.record["steps"][step_id].get("map_items", {})
                attempt.cost = float(sum((Decimal(str(item["cost_usd"])) for item in receipts.values()), Decimal(0)))
        attempt.context.report_cost(attempt.cost or 0.0)
        return results

    def _charge(self, run: _Run, attempt: _Attempt) -> float:
        if not attempt.dispatched:
            return 0.0
        if attempt.step.adapter.adapter_id == MAP and not attempt.task.done():
            # An in-flight child may already have incurred its full reservation.
            return run.plan.policies[attempt.step.step_id].cost_usd
        if attempt.cost is not None:
            return attempt.cost
        return run.plan.policies[attempt.step.step_id].cost_usd

    def _collect_finished(self, run: _Run, active: dict[str, _Attempt]) -> None:
        finished = sorted(((key, attempt) for key, attempt in active.items() if attempt.task.done()),
                          key=lambda item: item[1].completed_order)
        for step_id, attempt in finished:
            task = attempt.task
            del active[step_id]
            cost = self._charge(run, attempt)
            if task.cancelled():
                self._finish_step(run, step_id, "cancelled", run.stop_reason or "Step cancelled.", cost=cost)
                continue
            try:
                outputs = task.result()
            except ControlBlocked as exc:
                self._finish_step(run, step_id, "blocked", str(exc), cost=cost)
                run.stop(str(exc), "failed")
            except Exception:
                self._finish_step(run, step_id, "failed", "Step execution failed.", cost=cost)
            else:
                self._finish_step(run, step_id, "completed", outputs=outputs, cost=cost)

    async def _cancel_attempts(self, run: _Run, active: dict[str, _Attempt]) -> None:
        async def cancel_adapter(attempt: _Attempt) -> None:
            try:
                await run.plan.adapters[attempt.step.step_id].cancel(attempt.context)
            except Exception:
                pass

        hooks = []
        for attempt in active.values():
            if attempt.dispatched:
                hooks.append(asyncio.create_task(cancel_adapter(attempt)))
            attempt.task.cancel()
            attempt.task.add_done_callback(_observe)
        if hooks:
            _, pending = await asyncio.wait(hooks, timeout=1.0)
            for hook in pending:
                hook.cancel()
                hook.add_done_callback(_observe)
        for step_id, attempt in active.items():
            self._finish_step(run, step_id, "interrupted" if run.stop_status == "interrupted" else "cancelled",
                              run.stop_reason, cost=self._charge(run, attempt))
        active.clear()

    @staticmethod
    def _binding_state(run: _Run, source) -> str:
        if isinstance(source, WorkflowInputSource):
            return "completed" if source.port in run.plan.inputs else "absent"
        step = run.record["steps"][source.step_id]
        if step["status"] == "completed":
            if source.port in step["outputs"]:
                return "completed"
            return "skipped" if source.port in step.get("skipped_outputs", ()) else "absent"
        return step["status"]

    def _ready(self, run: _Run, spec: WorkflowStepSpec) -> bool:
        states = {self._binding_state(run, binding.source) for binding in spec.bindings}
        failed = states & {"failed", "cancelled", "blocked", "interrupted"}
        pending = states & {"pending", "running"}
        merge = spec.adapter.adapter_id == MERGE
        if merge and json.loads(spec.config_json).get("mode", "all") == "first":
            if "completed" in states:
                return True
            if pending:
                return False
        if not merge and "skipped" in states:
            self._finish_step(run, spec.step_id, "skipped", "An input path was skipped.")
            return False
        if pending:
            return False
        if failed:
            self._finish_step(run, spec.step_id, "blocked", "A dependency did not complete.")
            return False
        if merge and "completed" not in states:
            self._finish_step(run, spec.step_id, "skipped", "No input path was taken.")
            return False
        return True

    def _admit(self, run: _Run, active: dict[str, _Attempt], admitted: int) -> tuple[int, str | None]:
        record = run.record
        reserved = sum((Decimal(str(run.plan.policies[key].cost_usd)) for key in active), Decimal(0))
        lanes = {"local": 0, "cloud": 0}
        for key, attempt in active.items():
            if attempt.step.adapter.adapter_id == APPROVAL:
                return admitted, None
            if attempt.step.adapter.adapter_id != MAP:
                lanes[run.plan.policies[key].execution_lane] += 1
        blocked_cap = None
        for spec in run.plan.spec.steps:
            step_id = spec.step_id
            if step_id in active or record["steps"][step_id]["status"] != "pending":
                continue
            if not self._ready(run, spec):
                continue
            orchestrates = spec.adapter.adapter_id in (MAP, APPROVAL)
            if spec.adapter.adapter_id == APPROVAL and active:
                # Drain already-dispatched work before entering a human gate.
                # No other step (including a Map's children) runs during it.
                break
            attempts = 1
            if spec.adapter.adapter_id == MAP:
                attempts += (run.plan.preview.control_flow[step_id]["item_count"]
                             * len(run.plan.map_plans[step_id].spec.steps))
            if admitted + attempts > self.limits.max_steps:
                blocked_cap = f"Run stopped: step limit ({self.limits.max_steps}) reached."
                continue
            policy = run.plan.policies[step_id]
            capacity = self.limits.cloud_concurrency if policy.execution_lane == "cloud" else self.limits.local_concurrency
            if not orchestrates and lanes[policy.execution_lane] >= capacity:
                continue
            if Decimal(str(record["cost_usd"])) + reserved + Decimal(str(policy.cost_usd)) > Decimal(str(self.limits.cost_usd)):
                blocked_cap = "Run stopped: cost limit ($%.2f) reached." % self.limits.cost_usd
                continue
            active[step_id] = self._launch(run, spec)
            admitted += attempts
            reserved += Decimal(str(policy.cost_usd))
            if not orchestrates:
                lanes[policy.execution_lane] += 1
            if spec.adapter.adapter_id == APPROVAL:
                break
        return admitted, blocked_cap

    def _publish_outputs(self, run: _Run) -> None:
        for output in run.plan.spec.outputs:
            value = _source(run, output.source)
            if value is not None:
                if output.port.kind == value["kind"]:
                    run.record["outputs"][output.port.name] = value
                else:
                    run.record["outputs"][output.port.name] = self.artifacts.pack(
                        coerce_port(self.artifacts.unpack(value), output.port),
                    )

    async def _drive(self, run: _Run) -> dict[str, Any]:
        active: dict[str, _Attempt] = {}
        admitted = 0
        record = run.record
        deadline = asyncio.get_running_loop().time() + self.limits.wall_time_seconds
        record.update(status="running", started_at=utc_now())
        _event(record, "run-started")
        try:
            self._checkpoint(run)
            while True:
                self._collect_finished(run, active)
                if run.stop_reason is not None:
                    break
                if not active and all(s["status"] != "pending" for s in record["steps"].values()):
                    break
                now = asyncio.get_running_loop().time()
                remaining = deadline - now
                waiting = bool(run.approvals) or any(a.step.adapter.adapter_id == APPROVAL for a in active.values())
                if not waiting and remaining <= 0:
                    run.stop("Run stopped: duration limit reached.", "failed")
                    break
                admitted, cap = self._admit(run, active, admitted)
                if not active:
                    pending = [s for s in record["steps"].values() if s["status"] == "pending"]
                    if not pending:
                        break
                    if cap:
                        run.stop(cap, "failed")
                        break
                    if any(all(record["steps"][dep]["status"] != "pending"
                               for dep in run.plan.dependencies[s["step_id"]]) for s in pending):
                        continue
                    raise RuntimeError("Workflow scheduler made no progress.")
                waiting = any(a.step.adapter.adapter_id == APPROVAL for a in active.values())
                wait_start = asyncio.get_running_loop().time()
                wake = asyncio.create_task(run.wake.wait())
                try:
                    await asyncio.wait([wake, *(a.task for a in active.values())],
                                       timeout=None if waiting else remaining, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    wake.cancel()
                    await asyncio.gather(wake, return_exceptions=True)
                    if waiting:
                        deadline += asyncio.get_running_loop().time() - wait_start
            if active:
                await self._cancel_attempts(run, active)
            for step_id, step in record["steps"].items():
                if step["status"] == "pending":
                    self._finish_step(run, step_id, {"cancelled": "cancelled", "interrupted": "interrupted"}.get(run.stop_status, "blocked"),
                                      run.stop_reason or "Step was not run.")
            self._publish_outputs(run)
            if run.stop_reason is not None:
                status, reason = run.stop_status, run.stop_reason
            elif any(step["status"] not in ("completed", "skipped") for step in record["steps"].values()):
                status, reason = "failed", "One or more steps failed or were blocked."
            else:
                status, reason = "completed", None
        except asyncio.CancelledError:
            run.stop("The workflow was interrupted.", "interrupted")
            await self._cancel_attempts(run, active)
            status, reason = "interrupted", run.stop_reason
        except Exception:
            run.stop("The workflow could not finish.", "failed")
            await self._cancel_attempts(run, active)
            status, reason = "failed", run.stop_reason
        for step_id, step in record["steps"].items():
            if step["status"] in ("pending", "running"):
                self._finish_step(run, step_id, "interrupted" if status == "interrupted" else "blocked", reason)
        record.update(status=status, finished_at=utc_now(), error=reason)
        _event(record, f"run-{status}", message=reason or "")
        try:
            self._checkpoint(run)
            return record
        finally:
            self._runs.pop(record["run_id"], None)

    def save_result_as_graph(self, workspace_id: int, run_id: str, *, title: str | None = None) -> int:
        from .scratch_adapters import save_result_graph
        run = self.store.read(workspace_id, run_id)
        if run is None or run["status"] in ("pending", "running"):
            raise ValueError("Choose a retained, finished workflow run.")
        return save_result_graph(self.store.db_path, self.artifacts, workspace_id, run, title=title)
