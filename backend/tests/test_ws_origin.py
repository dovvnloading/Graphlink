"""WebSocket Origin allowlist tests (R5.4 security fix): guards /ws against
cross-site WebSocket hijacking ("localhost service takeover"), since browsers
do not enforce same-origin policy on WebSocket connects the way they do on
fetch/XHR. See backend/app.py's _is_allowed_ws_origin docstring for the full
policy rationale.

Split into two groups:
- Pure unit tests of _is_allowed_ws_origin (no ASGI needed).
- Integration tests through the real ASGI app (reuses make_client from
  test_app_ws.py rather than duplicating it).
"""

import pytest
from fastapi import WebSocketDisconnect

from backend.app import _is_allowed_ws_origin
from backend.tests.test_app_ws import make_client


# --- Pure unit tests of _is_allowed_ws_origin -------------------------------


def test_absent_origin_allowed():
    assert _is_allowed_ws_origin(None, "127.0.0.1:9999") is True
    assert _is_allowed_ws_origin("", "127.0.0.1:9999") is True


def test_same_origin_allowed():
    assert _is_allowed_ws_origin("http://127.0.0.1:9999", "127.0.0.1:9999") is True


def test_dev_proxy_origin_allowed_only_when_explicitly_opted_in():
    # host_header deliberately a *different* port, to prove this branch
    # doesn't depend on same-origin matching. Requires the caller to pass a
    # non-None dev_proxy_origin explicitly - the real ws_endpoint only does
    # this when GRAPHLINK_DEV_WS_ORIGIN is set, which is never true in the
    # shipped app (graphlink_desktop.py never sets it).
    assert _is_allowed_ws_origin(
        "http://127.0.0.1:5173", "127.0.0.1:9999", dev_proxy_origin="http://127.0.0.1:5173"
    ) is True


def test_dev_proxy_origin_rejected_by_default_when_not_opted_in():
    # Post-review fix: this exact port string used to be an unconditional,
    # hardcoded allow - live in the real shipped desktop app forever, not
    # just during the vite dev workflow. 5173 is Vite's own default dev port,
    # extremely common across unrelated projects - trusting it unconditionally
    # let any other local `npm run dev` tab talk to this backend. Now the
    # caller must explicitly opt in via dev_proxy_origin (omitted here, so it
    # defaults to None) before this origin is trusted at all.
    assert _is_allowed_ws_origin("http://127.0.0.1:5173", "127.0.0.1:9999") is False


def test_mismatched_origin_rejected():
    assert _is_allowed_ws_origin("http://evil.example.com", "127.0.0.1:9999") is False


def test_null_origin_rejected():
    assert _is_allowed_ws_origin("null", "127.0.0.1:9999") is False


def test_scheme_mismatch_rejected():
    # This app is http-only; proves the scheme is checked, not just the host.
    assert _is_allowed_ws_origin("https://127.0.0.1:9999", "127.0.0.1:9999") is False


def test_no_substring_bypass():
    assert _is_allowed_ws_origin("http://127.0.0.1:5173.evil.com", "127.0.0.1:9999") is False


def test_no_port_prefix_bypass():
    assert _is_allowed_ws_origin("http://127.0.0.1:51730", "127.0.0.1:5173") is False


def test_dns_rebinding_style_host_origin_self_match_rejected():
    # Post-review fix: comparing Origin to the request's own Host header is a
    # self-consistency check on two client-supplied values, not a check
    # against anything the server independently knows. A DNS-rebinding
    # attacker's page has Origin and Host both echo the SAME
    # attacker-controlled hostname (not 127.0.0.1) - requiring the host part
    # to literally be 127.0.0.1 closes this even though Origin == Host here.
    assert _is_allowed_ws_origin("http://evil.example.com:9999", "evil.example.com:9999") is False


# --- Integration tests through the real ASGI app ----------------------------


def test_ws_connect_missing_origin_succeeds():
    # No Origin header override - names the security decision explicitly
    # even though test_app_ws.py already covers this implicitly.
    client = make_client()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"kind": "intent", "topic": "system", "intent": "ping", "args": [], "id": 1})
        message = ws.receive_json()
        assert message["kind"] == "result"


def test_ws_connect_same_origin_succeeds():
    # Post-review fix: the same-origin branch now also requires the Host's
    # own hostname to literally be 127.0.0.1 (see _LOOPBACK_HOST), so this
    # must override TestClient's default "testserver" Host to exercise the
    # real production case (pywebview window + backend, always 127.0.0.1).
    client = make_client()
    headers = {"origin": "http://127.0.0.1:8000", "host": "127.0.0.1:8000"}
    with client.websocket_connect("/ws", headers=headers) as ws:
        ws.send_json({"kind": "intent", "topic": "system", "intent": "ping", "args": [], "id": 1})
        message = ws.receive_json()
        assert message["kind"] == "result"


def test_ws_connect_dev_proxy_origin_rejected_without_env_opt_in():
    # Post-review fix regression test: without GRAPHLINK_DEV_WS_ORIGIN set,
    # even Vite's own default dev port is just another untrusted origin - the
    # real gap the review found (this string was previously always trusted,
    # live in the shipped app, not just during the vite dev workflow).
    client = make_client()
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws", headers={"origin": "http://127.0.0.1:5173"}):
            pass
    assert exc_info.value.code == 1008
    assert client.app.state.bus.session("default").connection_count == 0


def test_ws_connect_dev_proxy_origin_succeeds_with_env_opt_in(monkeypatch):
    # Succeeds only once a developer explicitly opts in via
    # GRAPHLINK_DEV_WS_ORIGIN, and despite the client's own Host being
    # testserver (proving this branch doesn't depend on same-origin match).
    monkeypatch.setenv("GRAPHLINK_DEV_WS_ORIGIN", "http://127.0.0.1:5173")
    client = make_client()
    with client.websocket_connect("/ws", headers={"origin": "http://127.0.0.1:5173"}) as ws:
        ws.send_json({"kind": "intent", "topic": "system", "intent": "ping", "args": [], "id": 1})
        message = ws.receive_json()
        assert message["kind"] == "result"


def test_ws_connect_mismatched_origin_rejected():
    client = make_client()
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws", headers={"origin": "http://evil.example.com"}):
            pass
    assert exc_info.value.code == 1008
    # session.attach() was never reached, so no connection was ever
    # registered - confirms rejection happened before any intent could
    # possibly dispatch.
    assert client.app.state.bus.session("default").connection_count == 0


def test_ws_connect_null_origin_rejected():
    client = make_client()
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws", headers={"origin": "null"}):
            pass
    assert exc_info.value.code == 1008
    assert client.app.state.bus.session("default").connection_count == 0
