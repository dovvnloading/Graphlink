"""ADR-016 stage 16.4: redacted diagnostic bundle + open-log-folder.

The single most important test in this file is
test_bundle_never_contains_chat_content below - the literal, empirical proof
the ADR's own exit criterion demands ("Bundle proves no chat content
(redaction test)"), not an inference from reading build_diagnostic_bundle's
code.
"""

from __future__ import annotations

import inspect
import json
import os

import pytest

from backend.diagnostic_bundle import (
    BUNDLE_SCHEMA_VERSION,
    build_diagnostic_bundle,
    open_log_folder,
)
from backend.diagnostics import DiagnosticsState, provider_errors, record_provider_error, reset_provider_errors
from backend.domain.graph import SceneDocument
from graphlink_version import APP_VERSION

CANARY = "CANARY-SECRET-CHAT-CONTENT-DO-NOT-LEAK-12345"


@pytest.fixture(autouse=True)
def _isolated_provider_errors():
    """provider_errors() is module-global (see backend/diagnostics.py's own
    docstring) - reset around every test in this file so a canary recorded
    by one test can never leak into another test's bundle."""
    reset_provider_errors()
    yield
    reset_provider_errors()


# -- the redaction proof ------------------------------------------------------


def test_bundle_never_contains_chat_content():
    document = SceneDocument()
    document.add_chat_node(0, 0, CANARY, is_user=True)

    bundle = build_diagnostic_bundle(document, DiagnosticsState())

    serialized = json.dumps(bundle)
    assert CANARY not in serialized, (
        "the diagnostic bundle leaked chat content - the whole point of the "
        "builder's narrow (document, diagnostics_state) signature is that "
        "this can never happen"
    )


def test_bundle_never_contains_chat_content_even_with_a_run_history_present():
    # Belt-and-suspenders: the canary must stay absent even when the OTHER
    # allowlisted piece (diagnostics_state.payload()) is non-empty, proving
    # the two pieces don't somehow interact to leak the node's own content.
    document = SceneDocument()
    node = document.add_chat_node(0, 0, CANARY, is_user=True)
    diagnostics_state = DiagnosticsState()
    diagnostics_state.record_run_claimed("r-1", "chat", node.id)
    diagnostics_state.record_run_ended("r-1", "completed")

    bundle = build_diagnostic_bundle(document, diagnostics_state)

    assert CANARY not in json.dumps(bundle)
    # The run history legitimately carries the node id (not chat content -
    # see DiagnosticsState.payload()'s own docstring) - confirms the two
    # allowlisted pieces compose without the canary leaking through either.
    assert bundle["diagnostics"]["recentRuns"][0]["nodeId"] == node.id


def test_bundle_never_contains_chat_content_leaked_through_a_provider_error_message():
    """The field the two tests above never touch: providerErrors[].message
    is raw str(exc) from whatever exception fired during a chat call (see
    backend/diagnostics.py's own module docstring) - the one genuinely
    unredacted-by-construction field in the whole allowlist before
    record_provider_error's own redaction step. This is the realistic
    trigger: a provider SDK exception (or, concretely, an OSError raised by
    api_provider.py's _read_attachment_bytes when a chat attachment becomes
    unreadable) can embed literal chat content or an absolute filesystem
    path, and record_provider_error(provider, str(exc)) is called
    unconditionally, BEFORE any friendly-message translation. Without
    redaction at that choke point, this is exactly the leak this whole
    module's docstring claims is structurally impossible."""
    document = SceneDocument()
    document.add_chat_node(0, 0, "unrelated node content", is_user=True)
    record_provider_error("openai", f"request failed while sending: {CANARY}")

    bundle = build_diagnostic_bundle(document, DiagnosticsState())

    serialized = json.dumps(bundle)
    assert CANARY not in serialized, (
        "a provider error's raw exception text leaked into the diagnostic bundle - "
        "record_provider_error() must redact `message` before it is ever stored, "
        "not just before this builder's own signature is examined"
    )
    # Not just "the canary is gone" - the field itself must still be
    # present and non-empty, proving this is real redaction (a bounded,
    # fixed category label), not the field silently vanishing.
    assert bundle["diagnostics"]["providerErrors"] == provider_errors()
    assert bundle["diagnostics"]["providerErrors"][0]["provider"] == "openai"
    assert bundle["diagnostics"]["providerErrors"][0]["message"]


# -- structural-redaction signature guarantee ---------------------------------


def test_build_diagnostic_bundle_signature_accepts_exactly_document_and_diagnostics_state():
    # Positive assertion that nothing settings/API-key-shaped can even be
    # passed in - the builder's own signature IS the redaction mechanism
    # (see this module's docstring and diagnostic_bundle.py's own).
    params = list(inspect.signature(build_diagnostic_bundle).parameters)
    assert params == ["document", "diagnostics_state"], (
        f"build_diagnostic_bundle must take exactly (document, diagnostics_state) - got {params}. "
        "Widening this signature (e.g. adding a settings manager or raw log access) would defeat "
        "the whole structural-redaction design."
    )


# -- node-kind tally -----------------------------------------------------------


def test_node_counts_tally_by_kind_across_multiple_kinds():
    document = SceneDocument()
    document.add_chat_node(0, 0, "hello", is_user=True)
    document.add_chat_node(0, 0, "world", is_user=False)
    document.add_code_node(0, 0, "print(1)", "python")
    document.add_node(0, 0, "untitled")  # kind="placeholder"

    bundle = build_diagnostic_bundle(document, DiagnosticsState())

    assert bundle["nodeCounts"] == {"chat": 2, "code": 1, "placeholder": 1}


def test_node_counts_empty_document_is_an_empty_dict():
    bundle = build_diagnostic_bundle(SceneDocument(), DiagnosticsState())
    assert bundle["nodeCounts"] == {}


# -- allowlisted fields present -----------------------------------------------


def test_app_version_and_os_fields_are_present_and_non_empty():
    bundle = build_diagnostic_bundle(SceneDocument(), DiagnosticsState())

    assert bundle["appVersion"] == APP_VERSION
    assert bundle["appVersion"]

    assert bundle["os"]["system"]
    assert bundle["os"]["release"] is not None  # some platforms report ""; must at least be present
    assert bundle["os"]["pythonVersion"]


def test_bundle_schema_version_and_generated_at_are_present():
    bundle = build_diagnostic_bundle(SceneDocument(), DiagnosticsState())

    assert bundle["bundleSchemaVersion"] == BUNDLE_SCHEMA_VERSION
    assert isinstance(bundle["bundleSchemaVersion"], int)
    assert bundle["generatedAt"]  # ISO-8601 string; just needs to be present and non-empty


def test_diagnostics_payload_is_embedded_verbatim():
    diagnostics_state = DiagnosticsState()
    diagnostics_state.record_publish("scene", 512)

    bundle = build_diagnostic_bundle(SceneDocument(), diagnostics_state)

    assert bundle["diagnostics"] == diagnostics_state.payload()
    assert bundle["diagnostics"]["publishBytesTotal"] == 512


# -- open_log_folder: never raises, only ever True/False ---------------------


def test_open_log_folder_returns_false_when_os_startfile_raises(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")

    def _raising_startfile(_path):
        raise OSError("no shell association")

    monkeypatch.setattr(os, "startfile", _raising_startfile, raising=False)

    assert open_log_folder() is False


def test_open_log_folder_returns_false_on_non_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    # Sanity: even if startfile were somehow callable, os.name gates it -
    # give it a definitely-would-raise callable to prove the gate, not luck.
    monkeypatch.setattr(os, "startfile", lambda _path: (_ for _ in ()).throw(AssertionError("must not be called")), raising=False)

    assert open_log_folder() is False


def test_open_log_folder_returns_true_on_success(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(os, "startfile", lambda path: calls.append(path), raising=False)

    assert open_log_folder() is True
    assert len(calls) == 1
