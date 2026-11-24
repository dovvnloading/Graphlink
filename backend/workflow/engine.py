"""Detached version-pinned workflow scheduler; no active canvas mutations."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import secrets
import time
import uuid
from decimal import Decimal
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from backend.asset_store import store_for
from backend.canvas import SceneDocument
from .adapter import StepContext
from .artifacts import WorkflowArtifacts, canonical
from .contracts import PortValue, StepOutputSource, WorkflowInputSource, WorkflowSpec, WorkflowStepSpec
from .detached_store import DetachedRunStore, utc_now
from .planning import Plan, PolicyResolver, RunPreview, build_plan, default_policy
from .ports import coerce_port
from .registry import StepAdapterRegistry


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


@dataclass(frozen=True, slots=True)
class UnattendedPolicy:
    """An operator-reviewed exact preview and a restricted dispatch grant."""

    fingerprint: str
    allowed_effects: frozenset[str]
    cost_usd: float

    def __post_init__(self):
        if not self.allowed_effects <= {"pure", "local-read", "cloud"}:
            raise ValueError("Code execution and writes always require fresh operator approval.")
        if type(self.cost_usd) not in (int, float) or not math.isfinite(self.cost_usd) or self.cost_usd < 0:
            raise ValueError("Preset cost cap must be finite and nonnegative.")

    def permits(self, effect: str, policy) -> bool:
        return (effect in self.allowed_effects
                and effect not in ("executes", "writes")
                and (policy.execution_lane != "cloud" or "cloud" in self.allowed_effects))


@dataclass
class _Attempt:
    step: WorkflowStepSpec
    context: StepContext
    task: asyncio.Task | None = None
    dispatched: bool = False
    cost: float | None = None


@dataclass
class _Run:
    workspace_id: int
    plan: Plan
    record: dict[str, Any]
    limits: RunLimits
    unattended: UnattendedPolicy | None = None
    approved_steps: set[str] = field(default_factory=set)
    scratch: SceneDocument = field(default_factory=SceneDocument)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    stop_reason: str | None = None
    stop_status: str = "cancelled"
    task: asyncio.Task | None = None

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
                 recover: bool = True, on_pause: Callable[[int, str, str], None] | None = None):
        self.store = DetachedRunStore(db_path)
        self.on_pause = on_pause
        self.artifacts = WorkflowArtifacts(store_for(Path(db_path)))
        if registry is None:
            from .scratch_adapters import detached_registry
            from .library import library_registry
            registry = detached_registry(library_registry())
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
        self._previews[plan.preview.fingerprint] = (plan.preview, time.monotonic() + 900)
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
              *, approval_token: str | None = None, unattended: UnattendedPolicy | None = None,
              preset_id: str | None = None, operation_id: str | None = None) -> str:
        """Admit a frozen run, then schedule it; caller receives its cancel ID."""
        loop = asyncio.get_running_loop()
        spec = copy.deepcopy(spec)
        parameters = copy.deepcopy(dict(parameters))
        plan = build_plan(spec, parameters, self.registry, self.artifacts, self.policy_for)
        self._expire_approvals()
        approved_fingerprint = plan.preview.fingerprint
        # No await separates the active-run check from durable admission.
        # A paused run still owns the workflow, including across workspaces.
        if any(r.plan.spec.workflow_id == spec.workflow_id for r in self._runs.values()):
            raise ValueError("This workflow already has an active run.")
        limits = self.limits
        if unattended is not None:
            if approval_token is not None or unattended.fingerprint != approved_fingerprint:
                raise PermissionError("The unattended preview changed; review and save the preset again.")
            limits = replace(limits, cost_usd=min(limits.cost_usd, unattended.cost_usd))
        elif plan.preview.requires_approval or approval_token is not None:
            entry = self._approvals.pop(approval_token, None)
            if entry is None or entry[:2] != (approved_fingerprint, workspace_id):
                raise PermissionError("Approve this exact workflow preview before running it.")
        plan = build_plan(spec, parameters, self.registry, self.artifacts, self.policy_for, persist=True)
        if approved_fingerprint != plan.preview.fingerprint:
            raise PermissionError("Workflow preview changed; approve it again.")
        record = _new_record(plan, limits)
        if unattended is not None:
            record.update(preset_id=preset_id, operation_id=operation_id, awaiting_approval=None,
                          unattended_effects=sorted(unattended.allowed_effects))
        run = _Run(workspace_id, plan, record, limits, unattended)
        self.store.create(workspace_id, record)
        run_id = record["run_id"]
        self._runs[run_id] = run
        run.task = loop.create_task(self._drive(run))
        run.task.add_done_callback(_observe)
        return run_id

    def decide_step(self, workspace_id: int, run_id: str, step_id: str, nonce: str,
                    *, approved: bool) -> None:
        """Trusted operator-only decision on one exact pending step."""
        run = self._runs.get(run_id)
        pending = run.record.get("awaiting_approval") if run else None
        if (run is None or run.workspace_id != workspace_id or run.stop_reason is not None
                or not pending or pending["step_id"] != step_id or pending["nonce"] != nonce):
            raise ValueError("This step is no longer awaiting that approval.")
        run.record["awaiting_approval"] = None
        if approved:
            run.approved_steps.add(step_id)
        else:
            run.stop("Operator denied the workflow step.")
        _event(run.record, "step-progress", step_id=step_id,
               message="Operator approved step." if approved else "Operator denied step.")
        self._checkpoint(run)
        run.wake.set()

    def _pause(self, run: _Run, spec: WorkflowStepSpec) -> None:
        if run.record.get("awaiting_approval"):
            return
        pending = {"step_id": spec.step_id, "nonce": secrets.token_urlsafe(24),
                   "side_effect_class": spec.adapter.side_effect_class,
                   "preview_fingerprint": run.plan.preview.fingerprint}
        run.record["awaiting_approval"] = pending
        _event(run.record, "step-progress", step_id=spec.step_id,
               message="Waiting for operator approval.", data=pending)
        self._checkpoint(run)
        if self.on_pause is not None:
            try:
                self.on_pause(run.workspace_id, run.record["run_id"], spec.step_id)
            except Exception:
                # The durable pending receipt remains reviewable even if a notification fails.
                logging.getLogger(__name__).warning("Workflow approval notification could not be delivered.")

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

    async def shutdown(self) -> None:
        runs = list(self._runs.values())
        for run in runs:
            run.stop("The backend is shutting down.", "interrupted")
        await asyncio.gather(*(asyncio.shield(run.task) for run in runs), return_exceptions=True)

    def _checkpoint(self, run: _Run) -> None:
        self.store.checkpoint(run.workspace_id, run.record)

    def _finish_step(self, run: _Run, step_id: str, status: str, error: str | None = None,
                     outputs: dict[str, Any] | None = None, cost: float = 0.0) -> None:
        step = run.record["steps"][step_id]
        step.update(status=status, finished_at=utc_now(), error=error, cost_usd=cost)
        if outputs is not None:
            step["outputs"] = outputs
        run.record["cost_usd"] = float(sum(Decimal(str(s["cost_usd"])) for s in run.record["steps"].values()))
        _event(run.record, f"step-{status}", step_id=step_id, message=error or "")
        self._checkpoint(run)

    def _inputs(self, run: _Run, spec: WorkflowStepSpec) -> dict[str, PortValue]:
        ports = {port.name: port for port in spec.adapter.ports.inputs}
        result = {}
        for binding in spec.bindings:
            value = _source(run, binding.source)
            if value is not None:
                result[binding.port] = coerce_port(self.artifacts.unpack(value), ports[binding.port])
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
                run.stop("Run stopped: a step exceeded its approved cost estimate (cost limit).", "failed")

        context = StepContext(
            run.record["run_id"], spec.step_id, 1, scratch_document=run.scratch,
            artifacts=self.artifacts, model_identity=policy.model_identity,
            cost_limit_usd=policy.cost_usd, report_cost=report_cost,
        )
        attempt = _Attempt(spec, context)
        attempt.task = asyncio.create_task(self._execute(run, attempt))
        return attempt

    async def _execute(self, run: _Run, attempt: _Attempt) -> dict[str, Any]:
        spec = attempt.step
        step_id = spec.step_id
        policy = run.plan.policies[step_id]
        adapter = run.plan.adapters[step_id]
        async with self._lanes[policy.execution_lane]:
            if (run.unattended is not None and step_id not in run.approved_steps
                    and not run.unattended.permits(spec.adapter.side_effect_class, policy)):
                raise PermissionError("This unattended step has no operator approval.")
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

    def _charge(self, run: _Run, attempt: _Attempt) -> float:
        if not attempt.dispatched:
            return 0.0
        if attempt.cost is not None:
            return attempt.cost
        return run.plan.policies[attempt.step.step_id].cost_usd

    def _collect_finished(self, run: _Run, active: dict[str, _Attempt]) -> None:
        for step_id, attempt in list(active.items()):
            task = attempt.task
            if not task.done():
                continue
            del active[step_id]
            cost = self._charge(run, attempt)
            if task.cancelled():
                self._finish_step(run, step_id, "cancelled", run.stop_reason or "Step cancelled.", cost=cost)
                continue
            try:
                outputs = task.result()
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

    def _admit(self, run: _Run, active: dict[str, _Attempt], admitted: int) -> tuple[int, str | None]:
        record = run.record
        reserved = sum((Decimal(str(run.plan.policies[key].cost_usd)) for key in active), Decimal(0))
        lanes = {"local": 0, "cloud": 0}
        for key in active:
            lanes[run.plan.policies[key].execution_lane] += 1
        blocked_cap = None
        for spec in run.plan.spec.steps:
            step_id = spec.step_id
            if step_id in active or record["steps"][step_id]["status"] != "pending":
                continue
            statuses = {record["steps"][key]["status"] for key in run.plan.dependencies[step_id]}
            if statuses & {"failed", "cancelled", "blocked", "interrupted"}:
                self._finish_step(run, step_id, "blocked", "A dependency did not complete.")
                continue
            if statuses - {"completed"}:
                continue
            if admitted >= run.limits.max_steps:
                blocked_cap = f"Run stopped: step limit ({run.limits.max_steps}) reached."
                continue
            policy = run.plan.policies[step_id]
            capacity = run.limits.cloud_concurrency if policy.execution_lane == "cloud" else run.limits.local_concurrency
            if lanes[policy.execution_lane] >= capacity:
                continue
            if Decimal(str(record["cost_usd"])) + reserved + Decimal(str(policy.cost_usd)) > Decimal(str(run.limits.cost_usd)):
                blocked_cap = "Run stopped: cost limit ($%.2f) reached." % run.limits.cost_usd
                continue
            if (run.unattended is not None and step_id not in run.approved_steps
                    and not run.unattended.permits(spec.adapter.side_effect_class, policy)):
                # Drain already admitted work before stopping at the boundary.
                if not active:
                    self._pause(run, spec)
                return admitted, None
            active[step_id] = self._launch(run, spec)
            admitted += 1
            reserved += Decimal(str(policy.cost_usd))
            lanes[policy.execution_lane] += 1
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
        deadline = asyncio.get_running_loop().time() + run.limits.wall_time_seconds
        record.update(status="running", started_at=utc_now())
        _event(record, "run-started")
        try:
            self._checkpoint(run)
            while True:
                run.wake.clear()
                self._collect_finished(run, active)
                if run.stop_reason is not None:
                    break
                if not active and all(s["status"] != "pending" for s in record["steps"].values()):
                    break
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    run.stop("Run stopped: duration limit reached.", "failed")
                    break
                admitted, cap = self._admit(run, active, admitted)
                if not active and record.get("awaiting_approval"):
                    try:
                        await asyncio.wait_for(run.wake.wait(), timeout=remaining)
                    except TimeoutError:
                        run.stop("Run stopped: duration limit reached while awaiting approval.", "failed")
                    continue
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
                wake = asyncio.create_task(run.wake.wait())
                try:
                    await asyncio.wait([wake, *(a.task for a in active.values())],
                                       timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    wake.cancel()
                    await asyncio.gather(wake, return_exceptions=True)
            if active:
                await self._cancel_attempts(run, active)
            for step_id, step in record["steps"].items():
                if step["status"] == "pending":
                    self._finish_step(run, step_id, {"cancelled": "cancelled", "interrupted": "interrupted"}.get(run.stop_status, "blocked"),
                                      run.stop_reason or "Step was not run.")
            self._publish_outputs(run)
            if run.stop_reason is not None:
                status, reason = run.stop_status, run.stop_reason
            elif any(step["status"] != "completed" for step in record["steps"].values()):
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
        if run.unattended is not None:
            record["awaiting_approval"] = None
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
