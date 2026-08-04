"""ADR-004 stage 4.1: capability-token tests.

The exit criterion for this stage is "tokenless local WS/HTTP client is
rejected; window still works" - so the tests are organized around exactly
those two halves: every guarded surface must REJECT a caller without the
token, and must ACCEPT the app's own window (which presents it, in each of
the two forms the different browser APIs force on us).

The complementary invariant - that the shipped launch path always supplies
a real token, so create_app()'s test-friendly "None disables auth" default
can never silently become the shipped behavior - lives in
tests/test_graphlink_desktop.py, next to the code that mints it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.auth import (
    DEV_AUTH_TOKEN_ENV,
    extract_presented_token,
    is_guarded_path,
    mint_token,
    resolve_configured_token,
    token_matches,
)

TOKEN = "test-capability-token-abc123"


@pytest.fixture
def authed_client():
    return TestClient(create_app(auth_token=TOKEN))


# -- the unit layer: token primitives ---------------------------------------


def test_mint_token_is_long_and_unique_per_call():
    tokens = {mint_token() for _ in range(50)}
    assert len(tokens) == 50, "a per-launch capability must never repeat"
    # secrets.token_urlsafe(32) is ~43 chars; the floor guards against a
    # future refactor swapping in something trivially guessable.
    assert all(len(token) >= 32 for token in tokens)
    # URL-safe by construction, so it needs no escaping in the fragment the
    # desktop shell puts it in, nor in the query param the asset URLs use.
    assert all("=" not in token and "&" not in token and "#" not in token for token in tokens)


def test_token_matches_rejects_wrong_empty_and_none():
    assert token_matches(TOKEN, TOKEN) is True
    assert token_matches(TOKEN, "wrong") is False
    assert token_matches(TOKEN, "") is False
    assert token_matches(TOKEN, None) is False


def test_token_matches_does_not_raise_on_a_non_ascii_presented_token():
    # hmac.compare_digest raises TypeError on non-ASCII str. `presented` is
    # wholly attacker-controlled, so a raise here would surface as a 500 -
    # an oracle distinguishing "malformed" from "wrong" - instead of the
    # uniform 401 every other rejection produces.
    assert token_matches(TOKEN, "tökén-with-non-ascii") is False


def test_token_matches_rejects_a_prefix_of_the_real_token():
    # Guards the obvious catastrophic implementation slip (startswith rather
    # than a full comparison), which would let an attacker walk the token
    # one character at a time.
    assert token_matches(TOKEN, TOKEN[:-1]) is False
    assert token_matches(TOKEN, TOKEN + "x") is False


def test_extract_presented_token_reads_the_bearer_header_case_insensitively():
    assert extract_presented_token("Bearer abc", None) == "abc"
    assert extract_presented_token("bearer abc", None) == "abc"
    assert extract_presented_token("BEARER abc", None) == "abc"


def test_extract_presented_token_requires_the_bearer_scheme_but_falls_back_to_query():
    # A bare credential with no scheme is not accepted as the header form,
    # so an unrelated Authorization header can never accidentally match...
    assert extract_presented_token("abc", None) is None
    # ...but it must not short-circuit either: the two forms are
    # alternatives, so a malformed header still lets a valid query param
    # through rather than dead-ending the request.
    assert extract_presented_token("Basic dXNlcjpwdw==", "abc") == "abc"


def test_extract_presented_token_prefers_the_header_over_the_query_param():
    assert extract_presented_token("Bearer from-header", "from-query") == "from-header"


def test_is_guarded_path_covers_api_without_catching_lookalike_prefixes():
    assert is_guarded_path("/api/health") is True
    assert is_guarded_path("/api/assets/abc") is True
    assert is_guarded_path("/api") is True
    # The SPA bootstrap must stay reachable or the window can never load the
    # page that knows the token.
    assert is_guarded_path("/") is False
    assert is_guarded_path("/assets/index-abc.js") is False
    # Prefix confusion in both directions: a client-side route that merely
    # starts with "api" is not gated, and cannot be used to reach a real
    # /api route.
    assert is_guarded_path("/apidocs") is False
    assert is_guarded_path("/api-reference") is False


def test_resolve_configured_token_precedence(monkeypatch):
    monkeypatch.delenv(DEV_AUTH_TOKEN_ENV, raising=False)
    assert resolve_configured_token("explicit") == "explicit"
    assert resolve_configured_token(None) is None

    monkeypatch.setenv(DEV_AUTH_TOKEN_ENV, "from-env")
    assert resolve_configured_token(None) == "from-env"
    # An explicit token (what the desktop shell passes) always wins over the
    # dev escape hatch, so a stray env var on a developer's machine cannot
    # weaken a real launch.
    assert resolve_configured_token("explicit") == "explicit"


# -- the HTTP surface -------------------------------------------------------


def test_api_requests_without_a_token_are_rejected(authed_client):
    assert authed_client.get("/api/health").status_code == 401


def test_api_requests_with_a_wrong_token_are_rejected(authed_client):
    assert authed_client.get("/api/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert authed_client.get("/api/health?token=wrong").status_code == 401


def test_api_rejection_body_is_uniform_and_leaks_nothing(authed_client):
    no_token = authed_client.get("/api/health")
    wrong_token = authed_client.get("/api/health?token=wrong")
    malformed = authed_client.get("/api/health", headers={"Authorization": "Basic xyz"})

    # Identical status AND body for all three: never an oracle telling an
    # attacker whether they got the shape right, only the value wrong.
    assert no_token.status_code == wrong_token.status_code == malformed.status_code == 401
    assert no_token.json() == wrong_token.json() == malformed.json()
    assert TOKEN not in no_token.text


def test_api_requests_with_the_bearer_header_are_accepted(authed_client):
    response = authed_client.get("/api/health", headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_requests_with_the_query_param_are_accepted(authed_client):
    # The query-param form is what <img src="/api/assets/..."> must use -
    # the browser's image loader cannot send headers. Losing this breaks
    # every image and chart node, so it is pinned explicitly.
    assert authed_client.get(f"/api/health?token={TOKEN}").status_code == 200


def test_the_asset_routes_are_gated_too(authed_client):
    # /api/assets/* is the specific DNS-rebinding-reachable surface audit
    # finding C6 called out, so it gets its own assertion rather than
    # relying on /api/health standing in for the whole prefix.
    assert authed_client.get("/api/assets/some-asset-id").status_code == 401
    assert authed_client.get("/api/assets/chart/some-node/export").status_code == 401
    # With the token it reaches the real handler, which 404s on an unknown
    # asset - a DIFFERENT status, proving the request got past the gate
    # rather than being rejected for a second reason.
    assert authed_client.get(f"/api/assets/some-asset-id?token={TOKEN}").status_code == 404


def test_the_spa_bootstrap_is_not_gated(tmp_path):
    # The initial page load cannot carry a header, so if this were gated the
    # window could never reach the page that knows the token - the app would
    # be unbootable. Serves only public build output.
    spa_dir = tmp_path / "spa"
    (spa_dir / "assets").mkdir(parents=True)
    (spa_dir / "index.html").write_text("<html>graphlink</html>", encoding="utf-8")
    (spa_dir / "assets" / "index.js").write_text("console.log(1)", encoding="utf-8")

    client = TestClient(create_app(spa_dir=spa_dir, auth_token=TOKEN))

    assert client.get("/").status_code == 200
    assert client.get("/assets/index.js").status_code == 200


# -- the WebSocket surface --------------------------------------------------


def test_ws_handshake_without_a_token_is_rejected(authed_client):
    # THE audit-C5 case: a local process that never saw the token. The
    # origin check alone does not stop this - a non-browser caller simply
    # omits Origin, which _is_allowed_ws_origin deliberately permits.
    with pytest.raises(Exception):
        with authed_client.websocket_connect("/ws"):
            pass


def test_ws_handshake_with_a_wrong_token_is_rejected(authed_client):
    with pytest.raises(Exception):
        with authed_client.websocket_connect("/ws?token=wrong"):
            pass


def test_ws_handshake_with_the_query_param_is_accepted(authed_client):
    # The query form is mandatory for WS: the browser's WebSocket
    # constructor takes a URL and nothing else, so a header is not an
    # option on this handshake.
    with authed_client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        message = ws.receive_json()

    assert message["kind"] == "state"
    assert message["topic"] == "system"


def test_ws_handshake_with_the_bearer_header_is_accepted(authed_client):
    with authed_client.websocket_connect("/ws", headers={"Authorization": f"Bearer {TOKEN}"}) as ws:
        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        message = ws.receive_json()

    assert message["topic"] == "system"


def test_an_unauthenticated_ws_handshake_creates_no_session(authed_client):
    # Closed BEFORE accept(), so a tokenless caller cannot reach the audit-C6
    # session-growth vector either: every distinct ?session= value would
    # otherwise mint a permanent, never-evicted SessionBus (document +
    # dispatcher + autosave task).
    bus = authed_client.app.state.bus
    before = len(bus._sessions)

    with pytest.raises(Exception):
        with authed_client.websocket_connect("/ws?session=attacker-chosen-id"):
            pass

    assert len(bus._sessions) == before


# -- auth disabled (the test/dev default) -----------------------------------


def test_auth_disabled_when_no_token_is_configured(monkeypatch):
    monkeypatch.delenv(DEV_AUTH_TOKEN_ENV, raising=False)
    client = TestClient(create_app())

    assert client.app.state.auth_token is None
    assert client.get("/api/health").status_code == 200
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"kind": "subscribe", "topics": ["system"]})
        assert ws.receive_json()["topic"] == "system"


def test_the_dev_env_var_supplies_a_token_when_no_explicit_one_is_passed(monkeypatch):
    # The vite-dev workflow's escape hatch, matching the existing
    # GRAPHLINK_DEV_WS_ORIGIN precedent - unset in every real launch.
    monkeypatch.setenv(DEV_AUTH_TOKEN_ENV, "dev-token")
    client = TestClient(create_app())

    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health?token=dev-token").status_code == 200
