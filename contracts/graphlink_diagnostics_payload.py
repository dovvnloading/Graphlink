"""ADR-016 stage 16.3: the in-app diagnostics topic's wire contract.

Mirrors backend/diagnostics.py's DiagnosticsState.payload() field-for-field.
Local-only, in-memory, bounded (see that module's own docstring) - this
topic exists so a maintainer (or a curious user) can see recent run
outcomes, publish-size/rate, session count, and recent provider errors
live in the app, without a metrics pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DiagnosticsRunRowPayload:
    runId: str
    kind: str
    nodeId: str | None
    outcome: str  # "running" | "completed" | "cancelled"
    durationSeconds: float | None


@dataclass
class DiagnosticsProviderErrorPayload:
    provider: str
    message: str
    at: float  # time.time() epoch seconds


@dataclass
class DiagnosticsStatePayload:
    schemaVersion: int
    revision: int
    recentRuns: list[DiagnosticsRunRowPayload]
    publishCount: int
    publishBytesTotal: int
    lastPublishBytes: int | None
    lastPublishTopic: str | None
    publishBytesPerSecond: float
    sessionCount: int | None
    providerErrors: list[DiagnosticsProviderErrorPayload]
    minCompatibleSchemaVersion: int | None = None
