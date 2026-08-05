"""ADR-005 stage 5.4 (disclosure half): the real resource caps applied to
executed code, for CodeExecutionApprovalPanel.tsx's approval dialog.

That panel's WARNING_TEXT ("there is no sandboxing" / "isolates installed
packages, not the operating system") is honest but was never updated after
ADR-005 stages 5.2/5.3 added real resource caps and stage 5.5 restricted
dependency installs - this stage's own Decision §2 calls for "the approval
dialog states the actual limits". Deliberately NOT hardcoded in the
frontend: graphlink_execution_guard.py's caps are platform-conditional
(Windows: memory + active-process count only; POSIX: those two plus CPU
time and output file size - Windows has no CPU-rate control, see that
module's own stage 5.2 scoping note), so a hardcoded frontend string would
silently lie the moment the two diverge - the same class of staleness bug
ADR-004 stage 4.4's secretsEncryptedAtRest review caught.

Mirrors backend/about.py's own shape exactly: zero live state, one payload
function. The numbers this reads (graphlink_execution_guard.DEFAULT_*) are
module-level constants fixed for the life of the process, not anything that
could change mid-session, so - unlike graphlink_settings_store.py's DPAPI
probe - there is nothing here that needs live re-checking on every read.
"""

from __future__ import annotations

import sys
from typing import Any

import graphlink_execution_guard as guard
from backend.events import SessionBus

_DEPENDENCY_INSTALL_NOTE = (
    " Package installs are restricted to pre-built binary distributions - a "
    "package that only ships source code will fail to install rather than "
    "run its own build code during setup."
)


def _format_bytes(n: int) -> str:
    gib = n / (1024**3)
    if gib >= 1:
        return f"{gib:.0f} GB" if gib == int(gib) else f"{gib:.1f} GB"
    return f"{n // (1024**2)} MB"


_FAIL_OPEN_CAVEAT = (
    " These limits are applied automatically by this build and are not "
    "adjustable here; in rare cases (for example, security software "
    "blocking the underlying OS mechanism) they may not take effect."
)


def _resource_limits_sentence() -> str:
    """Windows has memory + active-process caps only (no CPU-rate control -
    see graphlink_execution_guard.py's own stage 5.2 scoping note for why
    that was left out entirely rather than shipped unverified). POSIX adds
    CPU time and output file size on top of those same two.

    Review-fix (ADR-005 stage 5.4): two wording changes, not just a shared
    trailing caveat, because two of the numbers here do not mean what an
    unqualified sentence would imply:

    - Windows's JobMemoryLimit bounds COMMITTED memory; POSIX's RLIMIT_AS
      bounds reserved ADDRESS SPACE, which large-but-unused virtual
      reservations (a multi-threaded interpreter's own stack reservations,
      BLAS/numpy thread pools) can exhaust well before actual usage
      approaches the same number. Saying "of memory" identically on both
      platforms implied a parity the two mechanisms do not have - POSIX
      now says "of reserved memory" instead.
    - RLIMIT_NPROC does not bound "this execution's own process tree" the
      way the Windows Job Object's active-process limit genuinely does -
      per POSIX semantics it bounds the total live process count for the
      REAL UID, system-wide (every process the desktop user owns, not just
      this run's descendants). Calling that "N concurrent processes" (as
      if scoped to the run) was a real, reachable inaccuracy - the POSIX
      sentence now describes it as capping the account's total process
      count instead.

    The trailing _FAIL_OPEN_CAVEAT covers the OTHER gap the review found:
    graphlink_execution_guard.py has several silent fail-open paths
    (Job Object creation/assignment failing under e.g. EDR policy, a
    per-rlimit setrlimit call being refused) that leave a real run
    completely uncapped with nothing surfaced beyond a logger.warning -
    this text cannot know, per-run, whether that happened (assign()'s
    failure modes only exist once a real process is being attached, so
    there is nothing to probe in advance), so it discloses the
    possibility honestly instead of asserting a guarantee stage 5.2's own
    empirical proof never actually covered for every failure mode."""
    memory = _format_bytes(guard.DEFAULT_MEMORY_LIMIT_BYTES)
    processes = guard.DEFAULT_ACTIVE_PROCESS_LIMIT
    if sys.platform == "win32":
        return (
            f"Execution is capped at approximately {memory} of memory and "
            f"{processes} concurrent processes; there is no CPU time limit."
        ) + _FAIL_OPEN_CAVEAT
    cpu_seconds = guard.DEFAULT_CPU_SECONDS
    file_size = _format_bytes(guard.DEFAULT_FILE_SIZE_LIMIT_BYTES)
    return (
        f"Execution is capped at approximately {memory} of reserved memory, "
        f"{cpu_seconds}s of CPU time, and {file_size} of output per file. "
        f"It also limits your user account's total live process count to "
        f"{processes} while it runs."
    ) + _FAIL_OPEN_CAVEAT


def execution_limits_payload() -> dict[str, Any]:
    resource_limits = _resource_limits_sentence()
    return {
        "pycoderResourceLimitsText": resource_limits,
        # ADR-005 stage 5.5: code_sandbox alone runs pip installs, so it
        # alone gets the --only-binary disclosure on top of the shared
        # resource-cap sentence.
        "codeSandboxResourceLimitsText": resource_limits + _DEPENDENCY_INSTALL_NOTE,
    }


def register_execution_limits(bus: SessionBus) -> None:
    bus.register_topic("execution-limits", execution_limits_payload)
