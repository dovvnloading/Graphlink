"""ADR-016 stage 16.4: redacted diagnostic bundle + open-log-folder.

REDACTION IS STRUCTURAL, NOT RUNTIME FILTERING. build_diagnostic_bundle's own
signature is the enforcement mechanism: it accepts exactly a SceneDocument and
a DiagnosticsState, nothing else - no SettingsManager, no raw log file access,
no provider/API-key object of any kind. There is no code path inside this
function that COULD reach an API key, a saved chat, or a node's own
content/text/title fields, because nothing that owns those values is ever
passed in. This mirrors the existing crash-report design's own posture
(backend/crash_recovery.py: an unhandled exception's traceback lands in
graphlink.log via the excepthook, never in a separate "here is your API key"
artifact) - "never chat content" is guaranteed by what the builder is even
capable of reading, not by a filter that could regress by one missed field.

The bundle contains exactly this allowlist, and nothing else:
  - bundleSchemaVersion: a plain int, bumped whenever this shape changes.
  - generatedAt: UTC ISO-8601 timestamp of when the bundle was built.
  - appVersion: graphlink_version.APP_VERSION (the same constant about.py's
    own about_payload() uses - never graphlink_update.py, which imports
    PySide6 at module scope; see backend/about.py's own docstring).
  - os: platform.system()/release()/python_version() - OS/interpreter
    identification, nothing environment- or account-specific.
  - nodeCounts: a tally of SceneNode.kind -> count, e.g. {"chat": 3,
    "code": 1}. COUNTS ONLY - this iterates document.nodes.values() and
    reads ONLY the `kind` attribute off each node; it never reads `content`,
    `title`, `history`, or any per-kind state field, so a chat node's actual
    message text can never reach the bundle through this path.
  - diagnostics: diagnostics_state.payload() embedded verbatim - itself
    already redacted by construction (backend/diagnostics.py's own
    DiagnosticsState.payload(): recentRuns carries only runId/kind/nodeId/
    outcome/durationSeconds, providerErrors only provider/message/at). The
    recentRuns fields are content-free by construction the same way
    nodeCounts is above. providerErrors' `message` is the one field that
    ISN'T inherently content-free - it starts life as raw str(exc) from
    whatever exception fired during a chat call, which can embed an
    absolute filesystem path or even literal chat content (see
    backend/diagnostics.py's own module docstring and
    _redact_provider_error_message) - so record_provider_error() itself
    redacts it down to one of a small, fixed vocabulary of category labels
    before it is ever stored, and it is THAT already-redacted value this
    function embeds verbatim, not the original exception text.

See backend/tests/test_diagnostic_bundle.py for the empirical proof: a
SceneDocument holding a chat node whose content is a canary string, with the
whole bundle serialized and searched for that string.
"""

from __future__ import annotations

import logging
import os
import platform
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from graphlink_version import APP_VERSION

from backend import crash_recovery

if TYPE_CHECKING:
    from backend.diagnostics import DiagnosticsState
    from backend.domain.graph import SceneDocument

logger = logging.getLogger(__name__)

BUNDLE_SCHEMA_VERSION = 1


def build_diagnostic_bundle(document: SceneDocument, diagnostics_state: DiagnosticsState) -> dict[str, Any]:
    """Assemble the redacted diagnostic snapshot for an issue report. See
    this module's own docstring for the exact allowlist and why this
    function's signature - exactly (document, diagnostics_state), nothing
    settings- or API-key-shaped - is itself the redaction guarantee."""
    node_kind_counts: dict[str, int] = {}
    for node in document.nodes.values():
        node_kind_counts[node.kind] = node_kind_counts.get(node.kind, 0) + 1

    return {
        "bundleSchemaVersion": BUNDLE_SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "appVersion": APP_VERSION,
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "pythonVersion": platform.python_version(),
        },
        "nodeCounts": node_kind_counts,
        "diagnostics": diagnostics_state.payload(),
    }


def open_log_folder() -> bool:
    """Opens the OS file browser at the log folder (Windows only). A
    convenience action, never allowed to crash the app: any failure - a
    non-Windows OS, a missing/unreadable directory, os.startfile itself
    raising - is caught here and reported as a plain False, never a raised
    exception reaching the caller."""
    try:
        if os.name != "nt":
            return False
        os.startfile(str(crash_recovery.log_path().parent))  # noqa: S606 - Windows-only, guarded above
        return True
    except Exception:
        logger.warning("Failed to open log folder", exc_info=True)
        return False
