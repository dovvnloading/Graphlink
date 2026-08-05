"""ADR-004 stage 4.6: the security-invariant suite named by that stage's own
exit criterion - "Invariant test suite in CI (auth-required, no-plaintext-
when-DPAPI-ok, loopback-only)". See doc/adr/THREAT_MODEL.md for the full
threat model these three invariants are drawn from.

This file is deliberately NOT a replacement for the exhaustive per-mechanism
coverage already in test_auth.py, test_http_trust_boundary.py,
test_ws_origin.py, and test_backend_secrets_at_rest.py - those own every
edge case (header casing, malformed tokens, dev-proxy escape hatches, ...).
This file states the three invariants THEMSELVES, once each, end-to-end
through the real backend.app.create_app() app object, so a reviewer (or a
future change) can read one place and see exactly what ADR-004 promises,
without inferring it from scattered implementation-detail assertions. Each
invariant gets one negative case (the attack is rejected) and one positive
case (the legitimate caller still works) - a suite that only ever asserts
rejection cannot distinguish "the boundary works" from "the boundary is
so broken nothing gets through at all".

Runs automatically in CI: .github/workflows/ci.yml's `python -m pytest -q`
step collects every file under backend/tests/ from the repo root - no
separate CI wiring was needed for this file to be "in CI".
"""

from __future__ import annotations

import pytest
from fastapi import WebSocketDisconnect

import graphlink_secrets
from backend.app import create_app
from fastapi.testclient import TestClient
from graphlink_settings_store import SettingsManager

TOKEN = "invariant-suite-test-token"


def _make_client(**create_app_kwargs) -> TestClient:
    # TrustedHostMiddleware (the loopback-only invariant itself) rejects
    # TestClient's default Host ("testserver"), and websocket_connect
    # hardcodes its own Host independent of base_url - both kwargs are
    # required for every other test in this file to even reach the checks
    # under test. Matches backend/tests/test_auth.py's authed_client fixture.
    return TestClient(
        create_app(auth_token=TOKEN, **create_app_kwargs),
        base_url="http://127.0.0.1",
        headers={"host": "127.0.0.1"},
    )


# -- Invariant 1: auth-required ----------------------------------------------
# Every /api/* request and the /ws handshake must present the per-launch
# capability token. Closes audit finding C5 (backend/auth.py's docstring):
# without this, any other local process - not just a browser page - can
# drive all registered intents, including approveCodeExecution.


class TestAuthIsRequired:
    def test_an_api_request_with_no_token_is_rejected(self):
        client = _make_client()

        response = client.get("/api/health")

        assert response.status_code == 401

    def test_a_ws_handshake_with_no_token_is_rejected(self):
        client = _make_client()

        # code == 1008 (policy violation) is ws_endpoint's own rejection,
        # not e.g. an unrelated crash elsewhere in connection setup that
        # would also raise WebSocketDisconnect but for the wrong reason.
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws"):
                pass
        assert exc_info.value.code == 1008

    def test_an_api_request_with_the_correct_token_is_accepted(self):
        # Positive control: proves the middleware is actually gating on the
        # token, not just rejecting everything that reaches it.
        client = _make_client()

        response = client.get(
            "/api/health", headers={"Authorization": f"Bearer {TOKEN}"}
        )

        assert response.status_code == 200

    def test_a_ws_handshake_with_the_correct_token_is_accepted(self):
        client = _make_client()

        with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
            ws.send_json({"kind": "subscribe", "topics": ["system"]})
            message = ws.receive_json()

        assert message["kind"] == "state"


# -- Invariant 2: no-plaintext-when-DPAPI-ok ---------------------------------
# Whenever DPAPI genuinely round-trips (dpapi_available() is True), no
# provider API key or GitHub token ever reaches disk unencrypted. Closes
# audit finding H12's core promise - the *visibility* half of H12 (the UI
# banner when DPAPI is unavailable) is covered by
# test_backend_secrets_at_rest.py's TestSecretsEncryptedAtRestFlag, not
# repeated here.
#
# DPAPI itself is monkeypatched rather than relying on the real Windows API,
# so this invariant is pinned deterministically on every platform CI might
# run on, not only Windows. The fake genuinely distinguishes the encrypt vs
# decrypt direction (tags encrypted bytes with a marker, strips-and-verifies
# it on decrypt, returns None - "decrypt failed" - on anything undecorated) -
# an identity round-trip (returning `data` unchanged regardless of the
# `encrypt` flag) would pass every test below even if protect() and
# unprotect() had their `encrypt=` arguments swapped, since the plaintext
# would still come back byte-for-byte either way.


_FAKE_DPAPI_MARKER = b"\xf0fake-dpapi-blob\xf0"


def _fake_dpapi_call(data: bytes, encrypt: bool) -> bytes | None:
    """Stands in for the real CryptProtectData/CryptUnprotectData round trip
    without touching the real Windows API - but, unlike a bare identity
    function, genuinely respects the encrypt/decrypt direction: encrypt tags
    the bytes, decrypt requires (and strips) that exact tag, returning None
    - "decryption failed", matching real _dpapi_call's own contract - for
    anything untagged. This is deliberate: an identity round-trip is blind to
    a regression that swaps protect()'s and unprotect()'s own `encrypt=`
    arguments (protect() calling _dpapi_call(..., encrypt=False) would still
    return its input unchanged and look like a successful encryption), since
    real bytes decrypted-as-if-already-encrypted would still just come back
    as themselves. This fake fails closed on that swap instead: decrypting
    unmarked (plaintext) bytes returns None, so protect() takes its own
    documented "DPAPI failed" fallback and returns the plaintext UNCHANGED -
    which the tests below then correctly catch as a plaintext leak."""
    if encrypt:
        return _FAKE_DPAPI_MARKER + data
    if not data.startswith(_FAKE_DPAPI_MARKER):
        return None
    return data[len(_FAKE_DPAPI_MARKER):]


class TestNoPlaintextWhenDpapiIsOk:
    @pytest.fixture(autouse=True)
    def _dpapi_genuinely_round_trips(self, monkeypatch):
        monkeypatch.setattr(graphlink_secrets, "_dpapi_call", _fake_dpapi_call)
        assert graphlink_secrets.dpapi_available() is True

    def test_a_saved_api_key_is_dpapi_prefixed_on_disk_not_plaintext(self, tmp_path):
        state_file = tmp_path / "session.dat"
        manager = SettingsManager(state_file)

        manager.set_api_settings(
            "OpenAI-Compatible",
            "https://api.openai.com/v1",
            "sk-openai-plaintext-marker",
            "sk-ant-plaintext-marker",
            "AIza-gemini-plaintext-marker",
        )

        raw = state_file.read_text(encoding="utf-8")
        assert "sk-openai-plaintext-marker" not in raw
        assert "sk-ant-plaintext-marker" not in raw
        assert "AIza-gemini-plaintext-marker" not in raw
        for field in ("openai_api_key", "anthropic_api_key", "gemini_api_key"):
            assert manager.state[field].startswith("dpapi:")

    def test_a_saved_github_token_is_dpapi_prefixed_on_disk_not_plaintext(self, tmp_path):
        state_file = tmp_path / "session.dat"
        manager = SettingsManager(state_file)

        manager.set_github_token("ghp_plaintext_marker_token")

        raw = state_file.read_text(encoding="utf-8")
        assert "ghp_plaintext_marker_token" not in raw
        assert manager.state["github_access_token"].startswith("dpapi:")

    def test_secrets_manager_reports_encrypted_at_rest_is_true(self, tmp_path):
        # secrets_encrypted_at_rest() is the flag the Settings UI banner
        # inverts on - it must agree with the real DPAPI state, not just the
        # on-disk prefix, since a caller could theoretically be checking
        # this instead of parsing the file.
        manager = SettingsManager(tmp_path / "session.dat")

        assert manager.secrets_encrypted_at_rest() is True

    def test_the_stored_key_still_decrypts_back_to_the_original_value(self, tmp_path):
        # Encryption-without-recoverability is not the invariant - a
        # dpapi-prefixed value that can never be read back would silently
        # "lose" every key the user configures.
        manager = SettingsManager(tmp_path / "session.dat")

        manager.set_api_settings(
            "OpenAI-Compatible", "https://api.openai.com/v1",
            "sk-roundtrip-me", "", "",
        )

        assert manager.get_openai_key() == "sk-roundtrip-me"


# -- Invariant 3: loopback-only ----------------------------------------------
# Every /api/* request and the /ws handshake must be addressed to (Host) and
# originate from (Origin) the loopback host - closes audit finding C6's
# concrete DNS-rebinding shape (TrustedHostMiddleware, on Host) and the
# complementary direct-cross-origin-fetch shape (require_loopback_origin, on
# Origin). Neither check alone is the invariant; both must hold together -
# see doc/adr/THREAT_MODEL.md's Tier 2 section for why they are independent.


class TestLoopbackOnly:
    def test_a_foreign_host_header_is_rejected_even_with_a_correct_token(self):
        client = _make_client()

        response = client.get(
            "/api/health",
            headers={"host": "evil.example.com", "Authorization": f"Bearer {TOKEN}"},
        )

        assert response.status_code == 400

    def test_a_foreign_origin_is_rejected_even_with_a_correct_token_and_host(self):
        client = _make_client()

        response = client.get(
            "/api/health",
            headers={"Authorization": f"Bearer {TOKEN}", "Origin": "http://evil.example.com"},
        )

        assert response.status_code == 403

    def test_a_ws_handshake_with_a_foreign_host_header_is_rejected(self):
        # This is TrustedHostMiddleware rejecting at the ASGI layer, before
        # ws_endpoint's own handler ever runs - it raises a
        # WebSocketDenialResponse (a WebSocketDisconnect subclass) with
        # status_code 400, NOT the .code == 1008 the app's own
        # websocket.close(1008) produces (see the sibling Origin test below,
        # and TestAuthIsRequired's no-token test) - asserting on the real,
        # distinct shape here is what proves THIS layer, not some other
        # coincidentally-passing exception, is what closed the connection.
        client = _make_client()

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                f"/ws?token={TOKEN}", headers={"host": "evil.example.com"}
            ):
                pass
        assert exc_info.value.status_code == 400

    def test_a_ws_handshake_with_a_foreign_origin_is_rejected(self):
        client = _make_client()

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                f"/ws?token={TOKEN}", headers={"Origin": "http://evil.example.com"}
            ):
                pass
        assert exc_info.value.code == 1008

    def test_the_loopback_host_and_same_origin_are_accepted(self):
        # Positive control: proves both checks are genuinely gating on
        # loopback-ness, not rejecting every request regardless of origin.
        client = _make_client()

        response = client.get(
            "/api/health",
            headers={"Authorization": f"Bearer {TOKEN}", "Origin": "http://127.0.0.1"},
        )

        assert response.status_code == 200
