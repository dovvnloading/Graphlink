"""ADR-005 stage 5.4 (disclosure half): the execution-limits topic's wire
contract.

CodeExecutionApprovalPanel.tsx's WARNING_TEXT ("there is no sandboxing" /
"isolates installed packages, not the operating system") is honest but was
never updated to mention the real resource caps ADR-005 stages 5.2/5.3
actually added, or stage 5.5's dependency-install restriction - Decision §2
of that ADR calls for "the approval dialog states the actual limits". This
topic is how: backend/execution_limits.py computes the real, platform-
correct sentences (Windows: memory + process count only; POSIX: those plus
CPU time and output file size - see graphlink_execution_guard.py's own
stage 5.2 scoping note on why Windows has no CPU-rate control) and the SPA
renders them verbatim, never assembling or hardcoding a limits claim itself.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExecutionLimitsStatePayload:
    schemaVersion: int
    revision: int
    codeSandboxResourceLimitsText: str
    minCompatibleSchemaVersion: int | None = None
