"""ADR-016 stage 16.4: the "diagnostics" topic's two WS intents.

The topic itself is registered in backend/app.py by stage 16.3
(`bus.register_topic("diagnostics", diagnostics.payload)`) - this module
adds intents ONTO that already-existing topic, it does not create a new one.
Both intents take no arguments and neither mutates the document, so neither
belongs on the undo stack (see tests/undo_classification.py's own entries
for these two).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from backend import crash_recovery, diagnostic_bundle
from backend.diagnostics import DiagnosticsState
from backend.domain.graph import SceneDocument
from backend.events import SessionBus


def register_diagnostics_intents(
    bus: SessionBus,
    document: SceneDocument,
    diagnostics_state: DiagnosticsState,
) -> None:
    async def export_diagnostic_bundle():
        """Builds the redacted bundle (backend/diagnostic_bundle.py - see
        its own docstring for the exact allowlist) and writes it to
        ~/.graphlink/diagnostics/bundle-<UTC timestamp>.json, so a user can
        find and attach it to an issue report without a terminal. Returns
        both the bundle dict (for an in-app "copy to clipboard" affordance)
        and the path it was written to (so the UI can tell the user where
        it landed)."""
        bundle = diagnostic_bundle.build_diagnostic_bundle(document, diagnostics_state)
        utcnow = datetime.now(timezone.utc)
        # backend.crash_recovery._data_dir() (not log_path()/crash_dir()) is
        # the shared ~/.graphlink root every one of those helpers is itself
        # built from - reused directly here so the bundle lands as a sibling
        # of graphlink.log and the crash/ directory, not a second, divergent
        # notion of "where this app's data lives".
        directory = crash_recovery._data_dir() / "diagnostics"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"bundle-{utcnow.strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        return {"bundle": bundle, "path": str(path)}

    async def open_log_folder():
        return {"opened": diagnostic_bundle.open_log_folder()}

    bus.register_intent("diagnostics", "exportDiagnosticBundle", export_diagnostic_bundle)
    bus.register_intent("diagnostics", "openLogFolder", open_log_folder)
