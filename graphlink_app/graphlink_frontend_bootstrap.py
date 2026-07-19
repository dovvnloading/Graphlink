"""Frontend build orchestration for source checkouts (migration plan section 3.7).

The Python entry point (graphlink_app.py) is the one command a developer runs -
it must never require a separate manual `npm run build` as a prerequisite.
This module checks whether web_ui/'s built output under assets/ is present
and fresh, and if not, orchestrates `npm ci` + `npm run build` before the
app constructs any widget that depends on those assets (WebIslandHost /
ComposerWebHost read them synchronously at construction time).

Non-negotiables (section 3.7): never `npm install` on an end user's machine;
never require network to start in any non-developer mode; staleness
detection is a cheap mtime check, not a rebuild every launch; bootstrap
failures are loud and actionable, never a silent fall back to a stale
bundle. A frozen (PyInstaller) build ships prebuilt assets and must never
touch Node, npm, or the network at runtime - ensure_frontend_built() is a
hard no-op the moment sys.frozen is set, checked before anything else here
runs.
"""

import logging
import os
import subprocess
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

from graphlink_paths import ASSETS_DIR, REPO_ROOT

logger = logging.getLogger(__name__)

WEB_UI_DIR = REPO_ROOT / "web_ui"

# Vite 6 (this workspace's bundler) requires Node ^18.0.0 || ^20.0.0 || >=22.0.0.
# The floor here is the newest of those three lines still under Node's own
# Maintenance-LTS end-of-life as of this writing (2026-07-19): 18 (EOL
# 2025-04-30) and 20 (EOL 2026-04-30) are both already past their own EOL by
# that date; 22 (EOL 2027-04-30) is the first line consistent with the same
# "not EOL" bar used to exclude 18 in the first place - re-derive this against
# https://nodejs.org/en/about/previous-releases if this file is touched again
# long after 2026, rather than assume 22 still holds.
#
# web_ui/package.json's "engines" field mirrors this number (advisory only -
# no engine-strict=true is set, so npm only warns on violation); web_ui/.nvmrc
# pins a specific newer version (currently 24) as the actually-recommended,
# actually-validated one for `nvm use` - see that file's own comment for why
# it's allowed to differ from this floor.
MIN_NODE_MAJOR = 22

# Directories/files inside web_ui/ that do not affect `vite build` output and
# should not trigger a rebuild if their mtime changes.
_STALENESS_IGNORED_DIR_NAMES = {"node_modules", "dist", ".vite", ".vite-temp"}
_STALENESS_IGNORED_FILENAME_MARKERS = (
    ".test.ts", ".test.tsx", "vitest.config.ts", "vitest.setup.ts", "eslint.config.js",
)

DEV_MODE_ENV_VAR = "GRAPHLINK_FRONTEND_DEV"

# Second, independent opt-in for the live dev-server-in-window path: the exact
# origin (e.g. "http://127.0.0.1:5173") WebIslandHost should load instead of
# the offline inlined bundle. Deliberately a SEPARATE variable from
# DEV_MODE_ENV_VAR rather than derived from it: "skip npm orchestration" and
# "point the app's own webview at a live local server" are different trust
# decisions (the second relaxes the WebEngine network sandbox), and a single
# leaked/inherited env var must never activate both. Both must be set for the
# live path to engage - see resolve_dev_server_origin().
DEV_SERVER_URL_ENV_VAR = "GRAPHLINK_FRONTEND_DEV_URL"

# The live path only ever targets a loopback Vite dev server; anything else is
# a misconfiguration (or an attempt to point the sandboxed webview somewhere
# it must never go) and fails closed.
_DEV_SERVER_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})

# One-shot guard for resolve_dev_server_origin()'s misconfiguration warnings:
# the WebEngine request interceptor re-resolves the origin on every
# intercepted request (see graphlink_webengine.py), so an unguarded
# logger.warning here would repeat once per subresource request.
_warned_dev_url_issues: set[str] = set()


def _warn_once(key: str, message: str, *args) -> None:
    if key in _warned_dev_url_issues:
        return
    _warned_dev_url_issues.add(key)
    logger.warning(message, *args)


class FrontendBootstrapError(RuntimeError):
    """An actionable, user-facing frontend bootstrap failure.

    Always raised with a message a developer can act on directly (what's
    missing, what to install, what command failed and why) - never a bare
    subprocess traceback.
    """


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _dev_mode_requested() -> bool:
    """True if GRAPHLINK_FRONTEND_DEV is set to a truthy value.

    Opt-in escape hatch for a developer already running `npm run dev`
    themselves in a separate terminal. Setting this skips the
    build-orchestration below entirely, so the app launches immediately
    against whatever assets/ already has, without this module fighting the
    developer's own build loop. Set ALONE, the app still loads the offline
    inlined bundle; additionally setting GRAPHLINK_FRONTEND_DEV_URL points
    the app's own window at the live dev server for in-app HMR - see
    resolve_dev_server_origin().
    """
    return os.environ.get(DEV_MODE_ENV_VAR, "").strip().lower() in ("1", "true", "yes", "on")


def resolve_dev_server_origin() -> str | None:
    """The exact origin ("http://host:port") a live WebIslandHost load may
    target, or None if the live-URL path is inactive.

    Requires BOTH env vars: GRAPHLINK_FRONTEND_DEV (its meaning above is
    unchanged - build-orchestration skip) and GRAPHLINK_FRONTEND_DEV_URL.
    GRAPHLINK_FRONTEND_DEV alone is today's normal dev loop and stays
    silent; GRAPHLINK_FRONTEND_DEV_URL alone is a likely misconfiguration
    and is warned about (once), not guessed at. The URL itself must be a
    plain http origin on a loopback host with an explicit port - anything
    else fails closed with a warning. A missing port is invalid rather than
    defaulted to 80: Vite is never on port 80, so guessing there would fail
    closed permanently and silently instead of surfacing the real mistake.

    Never returns non-None in a frozen build - checked first, before any
    env var is even read, mirroring ensure_frontend_built()'s hard bypass.
    graphlink_webengine.preview_url_is_allowed() independently re-checks
    sys.frozen on its own side as well; neither module trusts the other to
    have gated this (same defense-in-depth convention as graphlink_paths'
    local frozen re-derivation).
    """
    if _is_frozen():
        return None
    raw_url = os.environ.get(DEV_SERVER_URL_ENV_VAR, "").strip()
    if not raw_url:
        return None
    if not _dev_mode_requested():
        _warn_once(
            "url-without-flag",
            "%s is set but %s is not; both are required to load the live dev "
            "server in-window. The offline bundle will load instead.",
            DEV_SERVER_URL_ENV_VAR,
            DEV_MODE_ENV_VAR,
        )
        return None
    try:
        parsed = urlsplit(raw_url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
        scheme = parsed.scheme
    except ValueError:
        host, port, scheme = "", None, ""
    # port 0 is not a real listen port; treat it as "no explicit port" so a
    # stray http://127.0.0.1:0 fails closed rather than yielding a dead origin.
    if scheme != "http" or host not in _DEV_SERVER_ALLOWED_HOSTS or not port:
        _warn_once(
            f"invalid-url:{raw_url}",
            "%s=%r is not a valid http://127.0.0.1:<port> origin. The offline "
            "bundle will load instead.",
            DEV_SERVER_URL_ENV_VAR,
            raw_url,
        )
        return None
    return f"http://{host}:{port}"


def discover_islands() -> list[str]:
    """Every island this workspace knows about, derived from the directory
    structure (web_ui/src/islands/<name>/) rather than a hand-maintained
    list - adding a new island directory is automatically picked up here
    with no change to this module. Reads WEB_UI_DIR at call time (not a
    precomputed path) so tests can monkeypatch just that one module global
    for full isolation."""
    islands_dir = WEB_UI_DIR / "src" / "islands"
    if not islands_dir.is_dir():
        return []
    return sorted(p.name for p in islands_dir.iterdir() if p.is_dir())


def _should_ignore_filename(name: str) -> bool:
    return any(marker in name for marker in _STALENESS_IGNORED_FILENAME_MARKERS)


def _newest_source_mtime() -> float | None:
    """Newest mtime among every web_ui/ source file relevant to a build.
    Deliberately not scoped per-island (shared lib/ code affects every
    island's bundle) - the cost of an occasional unnecessary rebuild of an
    unaffected island is cheap; under-invalidating and shipping a stale
    bundle is the failure mode section 3.7 says must never happen.

    Walks with os.walk (pruning ignored directories, notably node_modules,
    in place) rather than Path.rglob, which has no way to skip descending
    into a directory before enumerating everything inside it - node_modules
    alone is hundreds of packages, and "cheap" per section 3.7 means this
    check should never need to touch any of them."""
    if not WEB_UI_DIR.is_dir():
        return None
    newest = None
    for dirpath, dirnames, filenames in os.walk(WEB_UI_DIR):
        dirnames[:] = [d for d in dirnames if d not in _STALENESS_IGNORED_DIR_NAMES]
        for filename in filenames:
            if _should_ignore_filename(filename):
                continue
            mtime = (Path(dirpath) / filename).stat().st_mtime
            if newest is None or mtime > newest:
                newest = mtime
    return newest


def _island_is_stale(island: str, newest_source_mtime: float | None) -> bool:
    index_html = ASSETS_DIR / island / "index.html"
    if not index_html.is_file():
        return True
    if newest_source_mtime is None:
        # No web_ui/ source tree at all (e.g. a constrained install shipping
        # only prebuilt assets without the frontend workspace) - nothing to
        # compare against, so trust whatever is already built rather than
        # attempt a build that has no source to build from.
        return False
    return newest_source_mtime > index_html.stat().st_mtime


def _node_modules_needs_install() -> bool:
    lockfile = WEB_UI_DIR / "package-lock.json"
    installed_marker = WEB_UI_DIR / "node_modules" / ".package-lock.json"
    if not installed_marker.is_file():
        return True
    if not lockfile.is_file():
        return True
    return lockfile.stat().st_mtime > installed_marker.stat().st_mtime


def _require_node_and_npm() -> tuple[str, str]:
    node_path = shutil.which("node")
    npm_path = shutil.which("npm")
    if not node_path or not npm_path:
        # Distinguish which one is actually missing - node without npm on PATH
        # is a real, distinct scenario (a broken PATH, or a Node install that
        # only exposed the node binary), and telling a developer to reinstall
        # Node.js when Node is already present and working sends them down
        # the wrong path entirely.
        if node_path and not npm_path:
            missing_what = "npm was"
        elif npm_path and not node_path:
            missing_what = "Node.js was"
        else:
            missing_what = "Node.js and npm were"
        raise FrontendBootstrapError(
            f"Graphlink's frontend (web_ui/) needs to be built, but {missing_what} not "
            "found on PATH.\n\n"
            f"Install Node.js {MIN_NODE_MAJOR} or newer from https://nodejs.org/ "
            "(the LTS release includes npm and is recommended), then run this app "
            "again.\n\n"
            f"Set {DEV_MODE_ENV_VAR}=1 to skip this check if you are running "
            "`npm run dev` yourself in web_ui/ and want the app to launch against "
            "whatever is already built."
        )

    try:
        version_output = subprocess.run(
            [node_path, "--version"], capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError) as exc:
        raise FrontendBootstrapError(
            f"Found Node.js at {node_path} but `node --version` failed: {exc}"
        ) from exc

    # version_output looks like "v24.11.1".
    try:
        major = int(version_output.lstrip("v").split(".", 1)[0])
    except ValueError as exc:
        raise FrontendBootstrapError(
            f"Could not parse Node.js version from `node --version` output: {version_output!r}"
        ) from exc

    if major < MIN_NODE_MAJOR:
        raise FrontendBootstrapError(
            f"Graphlink's frontend needs Node.js {MIN_NODE_MAJOR} or newer; found "
            f"{version_output} at {node_path}.\n\n"
            f"Install a current LTS release from https://nodejs.org/ and run this "
            "app again."
        )

    return node_path, npm_path


def _run_npm(npm_path: str, args: list[str], *, extra_env: dict[str, str] | None = None) -> None:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        subprocess.run(
            [npm_path, *args], cwd=WEB_UI_DIR, env=env, check=True,
            capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        command = " ".join(["npm", *args])
        raise FrontendBootstrapError(
            f"`{command}` failed (exit code {exc.returncode}) while building Graphlink's "
            f"frontend in {WEB_UI_DIR}.\n\n--- stdout ---\n{exc.stdout}\n--- stderr ---\n{exc.stderr}"
        ) from exc


def ensure_frontend_built() -> None:
    """Build whichever islands are missing or stale, unless this is a frozen
    build (hard bypass, checked first) or the developer opted out via
    GRAPHLINK_FRONTEND_DEV. Raises FrontendBootstrapError on any failure -
    callers must not swallow it, per section 3.7's "loud and actionable,
    never a silent fall back to a stale bundle" rule."""
    if _is_frozen():
        return
    if _dev_mode_requested():
        return

    islands = discover_islands()
    if not islands:
        return

    newest_source_mtime = _newest_source_mtime()
    stale = [name for name in islands if _island_is_stale(name, newest_source_mtime)]
    if not stale:
        return

    node_path, npm_path = _require_node_and_npm()

    if _node_modules_needs_install():
        _run_npm(npm_path, ["ci"])

    for island in stale:
        _run_npm(npm_path, ["run", "build"], extra_env={"GRAPHLINK_ISLAND": island})
