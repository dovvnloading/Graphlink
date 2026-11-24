"""Structural interface for workflow step adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeVar

from .contracts import JsonData, PortSet, PortValue, SideEffectClass, WorkflowStepSpec

if TYPE_CHECKING:
    from backend.canvas import SceneDocument
    from .artifacts import WorkflowArtifacts

PreparedT = TypeVar("PreparedT")


@dataclass(frozen=True, slots=True)
class StepContext:
    """Identity of a single execution attempt, including cancellation scope."""

    run_id: str
    step_id: str
    attempt: int
    scratch_document: SceneDocument | None = None
    artifacts: WorkflowArtifacts | None = None
    model_identity: tuple[str, str] | None = None
    cost_limit_usd: float = 0.0
    report_cost: Callable[[float], None] | None = None
    wait_for_approval: Callable[[str], Awaitable[None]] | None = None
    run_map: Callable[[dict, list], Awaitable[list]] | None = None


class StepAdapter(Protocol[PreparedT]):
    """An adapter supplies ports and a declared class, not execution policy.

    ports, prepare, fingerprint and preview must be pure. Prepare validates a frozen
    configuration and already-resolved typed inputs without effects.
    Fingerprints must be deterministic over the pinned adapter identity,
    configuration and input content, not mutable state or object identity.
    Preview describes the output shape without calling run or performing any
    external effects; pure adapters may compute the shape locally. Only run
    may perform declared external effects. Cancel is best effort for the
    specified attempt; it does not imply that effects were rolled back.
    """

    @property
    def side_effect_class(self) -> SideEffectClass: ...

    def ports(self, config_json: str) -> PortSet: ...

    def prepare(self, spec: WorkflowStepSpec, inputs: Mapping[str, PortValue]) -> PreparedT: ...

    def fingerprint(self, prepared: PreparedT) -> str: ...

    def preview(self, prepared: PreparedT) -> Mapping[str, JsonData]: ...

    async def run(self, prepared: PreparedT, context: StepContext) -> Mapping[str, PortValue]: ...

    async def cancel(self, context: StepContext) -> None: ...
