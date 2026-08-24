"""ADR-004 stage 4.2: TrustedHostMiddleware + require_loopback_origin tests.

The stage's own exit criterion is "Rebinding test against /api/assets fails
to read; WS parity tests pass" - see test_dns_rebinding_style_host_cannot_
read_an_asset_even_with_a_valid_token below for the literal case, and the
"-- WS parity --" section for TrustedHostMiddleware's effect on /ws (it
covers both HTTP and WebSocket ASGI scopes, confirmed by test here, not
assumed from documentation).

Two independent checks are under test, and they are NOT redundant with each
other - see backend/app.py's own require_loopback_origin docstring for the
full reasoning:
  - TrustedHostMiddleware (Host header) closes DNS rebinding: an attacker's
    own domain, briefly re-resolved to 127.0.0.1, makes the browser send
    that domain as Host even though the socket lands here.
  - require_loopback_origin (Origin header, reusing _is_allowed_ws_origin
    verbatim) closes the complementary case: a page on some OTHER real
    origin issuing a direct cross-origin fetch() straight at
    127.0.0.1:<port> - no rebinding needed, Host is legitimately
    127.0.0.1, but Origin is that other page's own.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.session_context import get_session_context

TOKEN = "trust-boundary-test-token"


def _make_client(**create_app_kwargs) -> TestClient:
    # See backend/tests/test_app_ws.py's make_client for why BOTH base_url
    # and headers= are required (websocket_connect hardcodes its own Host
    # independent of base_url - a Starlette TestClient quirk, confirmed via
    # a raw-ASGI-scope probe, not assumed).
    #
    # settings_state_file/chat_db_path: without an explicit override,
    # create_app() falls through to its real production defaults
    # (~/.graphlink/session.dat, ~/.graphlink/chats.db) - every one of this
    # file's ~14 callers would read AND rewrite the developer's real live
    # settings/chat data. A TemporaryDirectory here (not a tmp_path fixture
    # argument) matches test_assets.py's own make_client() exactly, since
    # this helper is called directly by test functions with no tmp_path
    # parameter of their own. **create_app_kwargs is applied last, so an
    # individual test can still override these if it ever needs to.
    state_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    state_path = Path(state_dir.name)
    kwargs = {
        "settings_state_file": state_path / "session.dat",
        "chat_db_path": state_path / "chats.db",
        **create_app_kwargs,
    }
    client = TestClient(
        create_app(auth_token=TOKEN, **kwargs),
        base_url="http://127.0.0.1",
        headers={"host": "127.0.0.1"},
    )
    client._state_tmpdir = state_dir  # type: ignore[attr-defined]
    return client


# -- TrustedHostMiddleware: the Host-header check ----------------------------


def test_a_foreign_host_header_is_rejected_on_api():
    client = _make_client()

    response = client.get(
        "/api/health",
        headers={"host": "evil.example.com", "Authorization": f"Bearer {TOKEN}"},
    )

    # Starlette's own TrustedHostMiddleware response - a real 401/403 body
    # is never reached, since this rejects before our own middleware chain
    # even runs (it is the outermost layer - see require_loopback_origin's
    # own docstring for why, verified empirically there).
    assert response.status_code == 400


def test_a_foreign_host_header_is_rejected_even_with_a_correct_token_and_origin():
    # Proves TrustedHostMiddleware is genuinely independent of - and cannot
    # be bypassed by satisfying - the OTHER two checks. A request this
    # "correct" in every other respect must still fail on Host alone.
    client = _make_client()

    response = client.get(
        "/api/health",
        headers={
            "host": "evil.example.com",
            "Authorization": f"Bearer {TOKEN}",
            "Origin": "http://evil.example.com",
        },
    )

    assert response.status_code == 400


def test_the_loopback_host_is_accepted():
    client = _make_client()

    response = client.get("/api/health", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200


def test_a_host_header_with_a_port_still_matches_since_the_port_is_stripped():
    # TrustedHostMiddleware compares only the hostname portion (matches
    # _LOOPBACK_HOST's own host_header.rsplit(":", 1)[0] convention below)
    # - the real app's port is a dynamically OS-assigned free port
    # (graphlink_desktop.py's _free_port()), so the check cannot require an
    # exact host:port match without hardcoding a port that changes every
    # launch.
    client = _make_client()

    response = client.get(
        "/api/health", headers={"host": "127.0.0.1:54321", "Authorization": f"Bearer {TOKEN}"}
    )

    assert response.status_code == 200


# -- TrustedHostMiddleware: WS parity (the stage's own named criterion) -----


def test_a_foreign_host_header_is_rejected_on_ws():
    client = _make_client()

    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/ws?token={TOKEN}", headers={"host": "evil.example.com"}
        ):
            pass


def test_the_loopback_host_is_accepted_on_ws():
    client = _make_client()

    with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        message = ws.receive_json()

    assert message["topic"] == "system"


# -- the stage's own named exit criterion ------------------------------------


def test_dns_rebinding_style_host_cannot_read_an_asset_even_with_a_valid_token():
    """THE literal case ADR-004 stage 4.2's exit criterion names: "Rebinding
    test against /api/assets fails to read".

    Simulates the attack precisely: a real image asset exists (so a
    same-origin request WOULD successfully read it - proving any rejection
    below is the Host check, not a coincidental 404), the request carries a
    fully valid capability token (proving the token alone is not sufficient
    once Host is wrong - defense in depth actually holds), and the ONLY
    thing wrong is the Host header being an attacker's own domain (the
    observable symptom of DNS rebinding: the attacker's page issues its
    request against ITS OWN hostname, now re-resolved to 127.0.0.1, so the
    socket lands here but the Host header still says otherwise).
    """
    client = _make_client()
    bus = client.app.state.bus
    document = get_session_context(bus.session("default")).canvas_document
    parent = document.add_node(0, 0, "parent")
    node = document.add_image_node(
        0, 0, b"fake-png-bytes", "a test image", parent.id, mime_type="image/png"
    )
    asset_id = node.state.image_asset_id

    legit = client.get(f"/api/assets/{asset_id}", headers={"Authorization": f"Bearer {TOKEN}"})
    assert legit.status_code == 200, "the asset must be genuinely readable same-origin, or this test proves nothing"
    assert legit.content == b"fake-png-bytes"

    rebinding = client.get(
        f"/api/assets/{asset_id}",
        headers={"host": "attacker-controlled-domain.com", "Authorization": f"Bearer {TOKEN}"},
    )
    assert rebinding.status_code == 400
    assert rebinding.content != b"fake-png-bytes"


# -- require_loopback_origin: the Origin-header check ------------------------


def test_absent_origin_is_allowed():
    # The normal case for every non-fetch caller (a plain <img> GET carries
    # no Origin - see backend/app.py's own docstring on why this branch
    # exists and why it does NOT reopen the rebinding hole TrustedHostMiddleware
    # already closes).
    client = _make_client()

    response = client.get("/api/health", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200


def test_same_origin_is_allowed():
    client = _make_client()

    response = client.get(
        "/api/health",
        headers={"Authorization": f"Bearer {TOKEN}", "Origin": "http://127.0.0.1"},
    )

    assert response.status_code == 200


def test_a_foreign_origin_is_rejected_even_with_a_correct_token_and_host():
    # THE case require_loopback_origin exists for, distinct from
    # TrustedHostMiddleware: Host is legitimately 127.0.0.1 (no rebinding),
    # but Origin is some other page's real origin - a direct cross-origin
    # fetch() at a guessed/known loopback port.
    client = _make_client()

    response = client.get(
        "/api/health",
        headers={"Authorization": f"Bearer {TOKEN}", "Origin": "http://evil.example.com"},
    )

    assert response.status_code == 403


def test_null_origin_is_rejected():
    client = _make_client()

    response = client.get(
        "/api/health", headers={"Authorization": f"Bearer {TOKEN}", "Origin": "null"}
    )

    assert response.status_code == 403


def test_the_dev_proxy_origin_is_rejected_without_env_opt_in():
    client = _make_client()

    response = client.get(
        "/api/health",
        headers={"Authorization": f"Bearer {TOKEN}", "Origin": "http://127.0.0.1:5173"},
    )

    assert response.status_code == 403


def test_the_dev_proxy_origin_is_accepted_with_env_opt_in(monkeypatch):
    # GRAPHLINK_DEV_WS_ORIGIN now also gates /api, despite its WS-only-
    # sounding name - see backend/app.py's DEV_WS_ORIGIN_ENV constant and
    # CONTRIBUTING.md for why: Vite's dev proxy rewrites the Host header it
    # forwards to match this backend's own address (changeOrigin), but
    # leaves Origin as the real page origin, so a proxied fetch() needs the
    # identical opt-in the WS handshake already required.
    monkeypatch.setenv("GRAPHLINK_DEV_WS_ORIGIN", "http://127.0.0.1:5173")
    client = _make_client()

    response = client.get(
        "/api/health",
        headers={"Authorization": f"Bearer {TOKEN}", "Origin": "http://127.0.0.1:5173"},
    )

    assert response.status_code == 200


def test_the_spa_bootstrap_is_not_gated_by_origin(tmp_path):
    # Mirrors test_auth.py's own test_the_spa_bootstrap_is_not_gated for the
    # token - the origin check is ALSO scoped to /api only (is_guarded_path),
    # not applied to the SPA bootstrap, for the same reason: a plain top-
    # level navigation typically carries no Origin at all, and even if it
    # did, gating the bootstrap on it would risk bricking the very page that
    # is supposed to load first.
    spa_dir = tmp_path / "spa"
    (spa_dir / "assets").mkdir(parents=True)
    (spa_dir / "index.html").write_text("<html>graphlink</html>", encoding="utf-8")
    client = TestClient(
        create_app(
            spa_dir=spa_dir,
            auth_token=TOKEN,
            settings_state_file=tmp_path / "session.dat",
            chat_db_path=tmp_path / "chats.db",
        ),
        base_url="http://127.0.0.1",
        headers={"host": "127.0.0.1"},
    )

    response = client.get("/", headers={"Origin": "http://evil.example.com"})

    assert response.status_code == 200


# -- middleware ordering (pins the layering, not just each check alone) -----


# -- CSP: img-src (finding markdown-image-exfil) -----------------------------


def _spa_client(tmp_path) -> TestClient:
    # Same fixture shape as test_the_spa_bootstrap_is_not_gated_by_origin
    # above: a real spa_dir so the `spa()` route's FileResponse branches
    # (not the "SPA build not found" fallback) are actually exercised.
    # favicon.ico deliberately sits at the SPA ROOT, not under assets/ - a
    # file under assets/ is served by the separate `app.mount("/assets",
    # StaticFiles(...))` mount registered above spa(), never reaching this
    # route at all, so it would not exercise the branch this file's own
    # test_a_built_asset_file_also_carries_the_csp needs.
    spa_dir = tmp_path / "spa"
    (spa_dir / "assets").mkdir(parents=True)
    (spa_dir / "index.html").write_text("<html>graphlink</html>", encoding="utf-8")
    (spa_dir / "assets" / "index.js").write_text("console.log(1)", encoding="utf-8")
    (spa_dir / "favicon.ico").write_text("fake-icon-bytes", encoding="utf-8")
    return TestClient(
        create_app(
            spa_dir=spa_dir,
            auth_token=TOKEN,
            settings_state_file=tmp_path / "session.dat",
            chat_db_path=tmp_path / "chats.db",
        ),
        base_url="http://127.0.0.1",
        headers={"host": "127.0.0.1"},
    )


def test_the_spa_bootstrap_carries_an_img_src_csp(tmp_path):
    # backend/app.py's own CONTENT_SECURITY_POLICY docstring for the full
    # mechanism: an unrestricted <img src> in LLM/web-authored markdown is a
    # silent, no-click data-exfiltration channel once rendered, since the
    # SPA document had no CSP at all to constrain which hosts an image may
    # be fetched from. This pins the header actually reaches the real page
    # load (GET /, no Origin - the normal top-level navigation case, same
    # as test_the_spa_bootstrap_is_not_gated_by_origin above).
    client = _spa_client(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    csp = response.headers.get("content-security-policy")
    assert csp is not None
    assert "img-src 'self' data:" in csp


def test_a_client_side_route_fallback_also_carries_the_csp(tmp_path):
    # The spa() route's OTHER FileResponse branch (deep-link fallback to
    # index.html for a path with no matching build file) - both branches
    # must carry the header, not just the literal "/" case.
    client = _spa_client(tmp_path)

    response = client.get("/some/client-side/route")

    assert response.status_code == 200
    assert "img-src 'self' data:" in response.headers.get("content-security-policy", "")


def test_a_built_asset_file_also_carries_the_csp(tmp_path):
    # The spa() route's OTHER FileResponse branch: an actual on-disk build
    # file served THROUGH this catch-all itself (not the separate /assets
    # StaticFiles mount, which is a different route entirely and never
    # reaches spa()'s own header-attaching code). Setting the header
    # unconditionally on every response this handler returns, rather than
    # only on "/", is what makes this branch safe too.
    client = _spa_client(tmp_path)

    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert "img-src 'self' data:" in response.headers.get("content-security-policy", "")


def test_a_bad_origin_is_rejected_before_the_token_is_even_checked():
    # Pins the actual, empirically-verified registration order (see
    # backend/app.py's require_loopback_origin docstring): a request wrong
    # in BOTH ways gets the origin-specific 403, not the token's 401 -
    # proving origin is checked first (the outer layer), not merely that
    # both checks independently exist.
    client = _make_client()

    response = client.get(
        "/api/health",
        headers={"Authorization": "Bearer wrong-token", "Origin": "http://evil.example.com"},
    )

    assert response.status_code == 403
