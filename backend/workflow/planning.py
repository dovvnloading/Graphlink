"""Validate the frozen DAG and pin everything needed for approval."""
from __future__ import annotations

import json
import math
from decimal import Decimal
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from .artifacts import WorkflowArtifacts, canonical, digest
from .contracts import (PortSpec, PortValue, StepOutputSource, WorkflowInputSource,
                        WorkflowSpec, WorkflowStepSpec, freeze_config)
from .ports import ALLOWED_COERCIONS, PORT_KINDS, coerce_port
from .registry import StepAdapterRegistry

Provider = Literal["cloud", "local"]


@dataclass(frozen=True, slots=True)
class StepPolicy:
    """Trusted host/adapter metadata, not a grant supplied by a workflow.

    cost_usd is the maximum authorized charge for this step, not just a
    typical price. A provider adapter must enforce the bound at dispatch.
    Model identities include the concrete provider and model, not aliases.
    """
    execution_lane: Provider = "local"
    model_identity: tuple[str, str] | None = None
    cost_usd: float = 0.0

    def __post_init__(self):
        if self.execution_lane not in ("cloud", "local"):
            raise ValueError("Unknown workflow provider class.")
        if not math.isfinite(self.cost_usd) or self.cost_usd < 0:
            raise ValueError("A workflow step needs a finite nonnegative cost bound.")
        if self.model_identity is not None and (
            not isinstance(self.model_identity, tuple) or len(self.model_identity) != 2
            or not all(isinstance(part, str) and part for part in self.model_identity)
        ):
            raise ValueError("Model identity must pin both provider and model.")


PolicyResolver = Callable[[WorkflowStepSpec], StepPolicy]


def default_policy(step: WorkflowStepSpec) -> StepPolicy:
    if step.adapter.side_effect_class == "cloud":
        raise ValueError("Cloud steps require a trusted provider and cost estimate.")
    return StepPolicy()


@dataclass(frozen=True, slots=True)
class RunPreview:
    workflow_hash: str
    parameters_json: str
    steps: tuple[tuple[str, str, StepPolicy], ...]
    estimated_cost_usd: float
    requires_approval: bool
    fingerprint: str


@dataclass(frozen=True, slots=True)
class Plan:
    spec: WorkflowSpec
    dependencies: dict[str, frozenset[str]]
    adapters: dict[str, Any]
    policies: dict[str, StepPolicy]
    inputs: dict[str, dict[str, Any]]
    preview: RunPreview


def _ports(ports: tuple[PortSpec, ...]) -> dict[str, PortSpec]:
    result = {}
    for port in ports:
        if not port.name or port.name in result or port.kind not in PORT_KINDS:
            raise ValueError("Invalid or duplicate workflow port.")
        result[port.name] = port
    return result


def _connection(source: PortSpec, destination: PortSpec) -> None:
    if source.kind != destination.kind and (source.kind, destination.kind) not in ALLOWED_COERCIONS:
        raise ValueError(f"Port {destination.name!r}: cannot coerce {source.kind} to {destination.kind}.")
    if destination.required and not source.required:
        raise ValueError(f"Required port {destination.name!r} cannot depend on an optional output.")


def _acyclic(dependencies: dict[str, frozenset[str]]) -> None:
    remaining = dict(dependencies)
    while remaining:
        ready = {step_id for step_id, deps in remaining.items() if not deps}
        if not ready:
            raise ValueError("Workflow contains a dependency cycle.")
        remaining = {step_id: deps - ready for step_id, deps in remaining.items() if step_id not in ready}


def build_plan(spec: WorkflowSpec, parameters: Mapping[str, PortValue],
               registry: StepAdapterRegistry, artifacts: WorkflowArtifacts,
               policy_for: PolicyResolver = default_policy, *, persist: bool = False) -> Plan:
    if spec.schema_version != 1:
        raise ValueError("Unsupported workflow schema version.")
    inputs = _ports(spec.inputs)
    _ports(tuple(output.port for output in spec.outputs))
    if set(parameters) - inputs.keys():
        raise ValueError("Unknown workflow parameter.")
    packed = {}
    for name, port in inputs.items():
        if name in parameters:
            packed[name] = artifacts.pack(coerce_port(parameters[name], port), persist=persist)
        elif port.required:
            raise ValueError(f"Missing required parameter {name!r}.")
    steps = {step.step_id: step for step in spec.steps}
    if len(steps) != len(spec.steps) or "" in steps:
        raise ValueError("Invalid or duplicate workflow step ID.")
    adapters, policies, output_ports, dependencies = {}, {}, {}, {}
    for step in spec.steps:
        descriptor = step.adapter
        config = json.loads(step.config_json)
        if not isinstance(config, dict) or freeze_config(config) != step.config_json:
            raise ValueError("Workflow step configuration must be canonical JSON.")
        if descriptor.side_effect_class not in ("pure", "local-read", "cloud", "executes", "writes"):
            raise ValueError("Unknown side-effect class.")
        if registry.describe(descriptor.adapter_id, descriptor.adapter_version, step.config_json) != descriptor:
            raise ValueError(f"Adapter descriptor changed for {step.step_id!r}.")
        adapters[step.step_id] = registry.resolve(descriptor.adapter_id, descriptor.adapter_version)
        policy = policy_for(step)
        if not isinstance(policy, StepPolicy):
            raise ValueError("Invalid workflow execution policy.")
        policies[step.step_id] = policy
        if descriptor.side_effect_class == "cloud" and policies[step.step_id].execution_lane != "cloud":
            raise ValueError("Cloud steps cannot use the local concurrency lane.")
        output_ports[step.step_id] = _ports(descriptor.ports.outputs)

    def source_port(source):
        if isinstance(source, WorkflowInputSource):
            port = inputs.get(source.port)
        elif isinstance(source, StepOutputSource):
            port = output_ports.get(source.step_id, {}).get(source.port)
        else:
            port = None
        if port is None:
            raise ValueError("Workflow binding refers to an unknown source port.")
        return port

    for step in spec.steps:
        ports = _ports(step.adapter.ports.inputs)
        bound = set()
        deps = set()
        for binding in step.bindings:
            if binding.port in bound or binding.port not in ports:
                raise ValueError("Invalid or duplicate workflow input binding.")
            bound.add(binding.port)
            _connection(source_port(binding.source), ports[binding.port])
            if isinstance(binding.source, StepOutputSource):
                deps.add(binding.source.step_id)
        if any(port.required and port.name not in bound for port in ports.values()):
            raise ValueError(f"Step {step.step_id!r} has an unbound required input.")
        dependencies[step.step_id] = frozenset(deps)
    for output in spec.outputs:
        _connection(source_port(output.source), output.port)
    _acyclic(dependencies)

    workflow_hash = digest(asdict(spec))
    entries = tuple((step.step_id, step.adapter.side_effect_class, policies[step.step_id]) for step in spec.steps)
    cost = float(sum(Decimal(str(policy.cost_usd)) for policy in policies.values()))
    approval = any(effect in ("cloud", "executes", "writes") or policy.execution_lane == "cloud"
                   for _, effect, policy in entries)
    pin = {"workflow_hash": workflow_hash, "parameters": packed,
           "steps": [(step_id, effect, asdict(policy)) for step_id, effect, policy in entries],
           "estimated_cost_usd": cost}
    preview = RunPreview(workflow_hash, canonical(packed), entries, cost, approval, digest(pin))
    return Plan(spec, dependencies, adapters, policies, packed, preview)
