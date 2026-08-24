"""ADR-016 stage 16.4's diagnostics intents (backend/api/intents_diagnostics.py)
- unit tests against a bare SessionBus/SceneDocument/DiagnosticsState, not the
full app.py wiring (see backend/tests/test_app_ws.py's own
test_diagnostics_intents_dispatch_through_the_real_bus for the "wired into
the real app" round trip; this file covers the module in isolation, in
particular the SECURITY-FIX (OBS-3) 0600 permissioning on the exported
bundle file).
"""

from __future__ import annotations

import asyncio
import stat
import sys
from pathlib import Path

import pytest

from backend import crash_recovery
from backend.api.intents_diagnostics import register_diagnostics_intents
from backend.diagnostics import DiagnosticsState
from backend.domain.graph import SceneDocument
from backend.events import SessionBus

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX chmod semantics only apply on POSIX")


def _make_bus() -> tuple[SessionBus, DiagnosticsState]:
    diagnostics_state = DiagnosticsState()
    bus = SessionBus("test")
    bus.register_topic("diagnostics", diagnostics_state.payload)
    document = SceneDocument()
    register_diagnostics_intents(bus, document, diagnostics_state)
    return bus, diagnostics_state


def test_export_diagnostic_bundle_writes_a_bundle_file(tmp_path, monkeypatch):
    monkeypatch.setattr(crash_recovery, "_data_dir", lambda *args, **kwargs: tmp_path)
    bus, _diagnostics_state = _make_bus()

    result = asyncio.run(bus.dispatch_intent("diagnostics", "exportDiagnosticBundle", []))

    written_path = Path(result["path"])
    assert written_path.exists()
    assert written_path.parent == tmp_path / "diagnostics"


# -- SECURITY-FIX (OBS-3): 0600 permissions on the exported bundle file -----


def test_export_diagnostic_bundle_chmods_the_file_to_0600_on_posix(tmp_path, monkeypatch):
    monkeypatch.setattr(crash_recovery, "_data_dir", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(crash_recovery.sys, "platform", "linux")
    calls = []
    monkeypatch.setattr(crash_recovery.os, "chmod", lambda path, mode: calls.append((path, mode)))
    bus, _diagnostics_state = _make_bus()

    result = asyncio.run(bus.dispatch_intent("diagnostics", "exportDiagnosticBundle", []))

    written_path = Path(result["path"])
    assert (written_path, 0o600) in calls


def test_export_diagnostic_bundle_does_not_chmod_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(crash_recovery, "_data_dir", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(crash_recovery.sys, "platform", "win32")
    calls = []
    monkeypatch.setattr(crash_recovery.os, "chmod", lambda path, mode: calls.append((path, mode)))
    bus, _diagnostics_state = _make_bus()

    asyncio.run(bus.dispatch_intent("diagnostics", "exportDiagnosticBundle", []))

    assert calls == []


def test_export_diagnostic_bundle_chmod_failure_is_logged_and_swallowed_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(crash_recovery, "_data_dir", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(crash_recovery.sys, "platform", "linux")

    def _boom(path, mode):
        raise OSError("permission denied")

    monkeypatch.setattr(crash_recovery.os, "chmod", _boom)
    bus, _diagnostics_state = _make_bus()

    result = asyncio.run(bus.dispatch_intent("diagnostics", "exportDiagnosticBundle", []))  # must not raise

    assert Path(result["path"]).exists()


@POSIX_ONLY
def test_the_real_bundle_file_is_actually_0600_on_posix(tmp_path, monkeypatch):
    monkeypatch.setattr(crash_recovery, "_data_dir", lambda *args, **kwargs: tmp_path)
    bus, _diagnostics_state = _make_bus()

    result = asyncio.run(bus.dispatch_intent("diagnostics", "exportDiagnosticBundle", []))

    written_path = Path(result["path"])
    assert stat.S_IMODE(written_path.stat().st_mode) == 0o600
