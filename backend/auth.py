"""ADR-004 stage 4.1: the per-launch capability token that gates the whole
local API surface.

THE THREAT (audit finding C5). Graphlink binds a loopback HTTP/WS port with
no authentication of any kind, so ANY other process running as the same user
- a background updater, a rogue npm postinstall script, a browser extension's
native host, anything - can open /ws and drive all 131 intents. That includes
runCodeSandbox, applyGitlinkChanges, and (critically) approveCodeExecution:
the human-approval gate is itself just another intent on the same
unauthenticated channel, so a non-browser caller can approve its own
code execution. The existing WS Origin check (backend/app.py's
_is_allowed_ws_origin) is a genuinely good defense and stays, but it defends a
DIFFERENT threat - a malicious PAGE in the user's browser, which cannot forge
Origin. It does nothing against a local process, which can send any Origin
header it likes, or none at all (the "absent Origin" branch is deliberately
allowed there, for reasons that comment explains).

THE FIX. graphlink_desktop.py mints a fresh 256-bit token per launch, hands it
to create_app(), and passes it to the webview as a URL fragment. Every /api/*
request and the /ws handshake must present it. A local process that never saw
the token is rejected; the app's own window has it. Nothing is persisted -
the token exists only in the running process and the window it spawned, so
there are no accounts, no credential files, and no revocation story to get
wrong. This is a capability, not an identity.

WHY A FRAGMENT (rather than a header or a cookie) for delivery to the SPA.
The initial navigation to http://127.0.0.1:<port>/ is a plain browser page
load - there is no way to attach a header to it, so the bootstrap HTML and
its static assets are necessarily unauthenticated (they are also the same
public build output shipped in the wheel, so they disclose nothing). A URL
fragment is the narrowest way to get a secret INTO that page: fragments are
never sent to the server, so the token cannot land in an access log or a
Referer header, and pywebview's window has no address bar to display it.

TWO PRESENTATION FORMS, deliberately. `Authorization: Bearer <token>` is the
normal one, used by fetch() and the WS URL's own query string. The `?token=`
query parameter exists because <img src="/api/assets/..."> cannot set headers
- image-node and chart bytes are loaded by the browser's own image loader,
not by our JS - and ADR-004 §1 calls for exactly this ("header or signed query
for <img>-reachable asset URLs"). On a loopback-only port, with an ephemeral
per-launch token and no server-side request logging of query strings, the
usual objections to secrets-in-URLs do not apply here.
"""

from __future__ import annotations

import hmac
import os
import secrets

# 32 bytes = 256 bits, per ADR-004 §1. token_urlsafe returns ~43 URL-safe
# ASCII characters for this length, so it needs no escaping in a query
# string or a URL fragment.
TOKEN_BYTES = 32

AUTH_HEADER = "authorization"
BEARER_PREFIX = "bearer "
AUTH_QUERY_PARAM = "token"

# The dev-workflow escape hatch, matching the GRAPHLINK_DEV_WS_ORIGIN
# precedent already established for the Origin check (backend/app.py): a
# developer running `npm run dev` against a separately-started backend has no
# desktop shell to mint a token for them, so they set this to any value and
# use the same value from the client. Unset in every real launch - the
# desktop shell always passes an explicitly minted token instead.
DEV_AUTH_TOKEN_ENV = "GRAPHLINK_DEV_AUTH_TOKEN"

# Only /api/* is gated. The SPA bootstrap (GET /, /assets/* static files, and
# the client-side-route catch-all) must stay reachable without a token or the
# window could never load the page that KNOWS the token - see the module
# docstring. Those routes serve only the public build output.
API_PATH_PREFIX = "/api/"


def mint_token() -> str:
    """A fresh capability token. Called once per launch by
    graphlink_desktop.py - never persisted, never reused across launches."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def resolve_configured_token(explicit_token: str | None) -> str | None:
    """The token this app instance will require, or None for "auth disabled".

    Precedence, highest first:
      1. `explicit_token` - what graphlink_desktop.py passes. The real
         shipped path.
      2. $GRAPHLINK_DEV_AUTH_TOKEN - the dev-server escape hatch (see
         DEV_AUTH_TOKEN_ENV).
      3. None - auth disabled. This is what a bare create_app() in a test
         gets, so the ~1200-test suite needs no token plumbing to exercise
         unrelated behavior. create_app() logs a warning in this case, and
         tests/test_graphlink_desktop.py asserts the real launch path always
         reaches case 1 - so "shipped with auth accidentally off" is a
         test failure, not a silent regression.
    """
    if explicit_token:
        return explicit_token
    env_token = os.environ.get(DEV_AUTH_TOKEN_ENV)
    if env_token:
        return env_token
    return None


def extract_presented_token(
    header_value: str | None, query_token: str | None
) -> str | None:
    """Pull the token out of a request, header first then query parameter.

    The header form tolerates any casing of the "Bearer" scheme (RFC 6750
    says the scheme is case-insensitive) but requires the scheme to be
    present - a bare `Authorization: <token>` is not accepted, so a caller
    cannot accidentally match by sending some unrelated credential.
    """
    if header_value:
        if header_value.lower().startswith(BEARER_PREFIX):
            candidate = header_value[len(BEARER_PREFIX):].strip()
            if candidate:
                return candidate
        # A present-but-malformed Authorization header falls through to the
        # query parameter rather than short-circuiting to "no token": the two
        # forms are alternatives, not a priority chain that can dead-end.
    if query_token:
        return query_token
    return None


def token_matches(expected: str, presented: str | None) -> bool:
    """Constant-time comparison, so a local attacker cannot recover the token
    byte-by-byte from response timing.

    Encodes to bytes first: hmac.compare_digest raises TypeError on a str
    containing non-ASCII, and `presented` is wholly attacker-controlled - a
    raise here would surface as a 500 (an oracle distinguishing "malformed"
    from "wrong") instead of a uniform 401.
    """
    if not presented:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), presented.encode("utf-8"))


def is_guarded_path(path: str) -> bool:
    """True for the paths the capability token gates - /api/* only.

    Exact-prefix matching on "/api/", so a client-side SPA route that merely
    STARTS with the letters "api" (e.g. "/apidocs", or a future
    "/api-reference" marketing page) is not accidentally gated, and - more
    importantly - cannot be used to reach a real /api route by prefix
    confusion in the other direction.
    """
    return path == "/api" or path.startswith(API_PATH_PREFIX)
