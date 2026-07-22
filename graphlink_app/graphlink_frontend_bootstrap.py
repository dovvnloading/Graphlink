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
import signal
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


# Generous ceilings so a wedged npm can never hang startup forever with no
# window and no error - `subprocess.run` without a timeout blocks
# indefinitely, which presented as the app "looping" at launch. A clean
# `vite build` is sub-second per island; `npm ci` is minutes at worst.
_NPM_INSTALL_TIMEOUT_SECONDS = 600
_NPM_BUILD_TIMEOUT_SECONDS = 180


def _subprocess_no_window_kwargs() -> dict:
    # A windowed (pythonw / desktop-shortcut) launch otherwise flashes one
    # visible console window PER subprocess call - with every island stale
    # that strobed 19 consoles in a row before any app window appeared,
    # which read as the app stuck in an npm loop.
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


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
            encoding="utf-8", errors="replace",
            timeout=30, **_subprocess_no_window_kwargs(),
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
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


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Kill `process` AND every descendant it spawned.

    `subprocess`'s own kill (what `run(timeout=...)` does internally) signals
    only the *direct* child. Here that child is `cmd.exe` (npm ships as
    `npm.CMD`) and the real `node`/vite build is a grandchild that inherited
    our stdout pipe - so killing just `cmd.exe` leaves `node` running and
    holding the pipe open, and the post-kill read then blocks forever waiting
    for an EOF that never comes. That was the whole defect: the timeout never
    actually bounded launch on Windows. Killing the tree closes the pipe and
    lets the drain read finish.
    """
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        # taskkill /T walks the child tree by parent-PID; /F forces it. Always
        # present on Windows. CREATE_NO_WINDOW so this cleanup does not itself
        # flash a console window in a windowed (pythonw) launch.
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return
        except (OSError, subprocess.SubprocessError):
            pass  # fall through to the direct-child best effort below
    else:
        # _run_npm starts the child in its own session (start_new_session),
        # so the whole build tree shares one process group we can signal at
        # once. getpgid can race the process exiting - treat that as done.
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            return
        except (OSError, ProcessLookupError):
            pass
    # Last resort if the tree-kill was unavailable: at least signal the direct
    # child. Better than nothing, though a grandchild may still linger.
    try:
        process.kill()
    except OSError:
        pass


def _run_npm(
    npm_path: str,
    args: list[str],
    *,
    extra_env: dict[str, str] | None = None,
    timeout_seconds: int = _NPM_BUILD_TIMEOUT_SECONDS,
) -> None:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    command = " ".join(["npm", *args])

    popen_kwargs = dict(
        cwd=WEB_UI_DIR, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        # Decode explicitly as UTF-8: the OS default on Windows is cp1252,
        # which mojibakes npm's UTF-8 output and can raise an uncaught
        # UnicodeDecodeError on its undefined bytes - aborting launch with a
        # bare traceback. errors="replace" keeps the message readable no
        # matter what npm emits.
        text=True, encoding="utf-8", errors="replace",
        **_subprocess_no_window_kwargs(),
    )
    if sys.platform != "win32":
        # Own session/process group so _terminate_process_tree can take down
        # the whole build tree at once via killpg. On Windows taskkill /T
        # walks the PID tree directly, so no group is needed there.
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen([npm_path, *args], **popen_kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        # This used to be subprocess.run's job, but its timeout kills only the
        # direct child (cmd.exe) and then re-blocks on the pipe the surviving
        # node grandchild still holds - so the timeout never fired and launch
        # hung forever with no window and no error. Kill the entire tree, then
        # drain with a hard cap so a wedged grandchild can't re-hang the read.
        _terminate_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        raise FrontendBootstrapError(
            f"`{command}` did not finish within {timeout_seconds}s while building "
            f"Graphlink's frontend in {WEB_UI_DIR} - npm appears hung.\n\n"
            f"Try running the command manually in web_ui/ (deleting node_modules/ "
            f"and re-running often clears a wedged install), or set "
            f"{DEV_MODE_ENV_VAR}=1 to skip build orchestration if you manage the "
            f"frontend build yourself.\n\n--- partial stdout ---\n{stdout or ''}"
            f"\n--- partial stderr ---\n{stderr or ''}"
        )

    if process.returncode != 0:
        raise FrontendBootstrapError(
            f"`{command}` failed (exit code {process.returncode}) while building Graphlink's "
            f"frontend in {WEB_UI_DIR}.\n\n--- stdout ---\n{stdout or ''}\n--- stderr ---\n{stderr or ''}"
        )


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

    logger.info(
        "Frontend bootstrap: rebuilding %d stale island(s): %s",
        len(stale), ", ".join(stale),
    )

    if _node_modules_needs_install():
        logger.info("Frontend bootstrap: running `npm ci` first (node_modules missing or outdated)")
        _run_npm(npm_path, ["ci"], timeout_seconds=_NPM_INSTALL_TIMEOUT_SECONDS)

    for index, island in enumerate(stale, start=1):
        logger.info("Frontend bootstrap: building island %d/%d: %s", index, len(stale), island)
        _run_npm(npm_path, ["run", "build"], extra_env={"GRAPHLINK_ISLAND": island})

    logger.info("Frontend bootstrap: all %d island build(s) finished", len(stale))
