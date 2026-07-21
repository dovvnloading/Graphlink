"""Coverage for graphlink_frontend_bootstrap.py (Phase 1 checklist: frontend
bootstrap skeleton, migration plan section 3.7).

Every test isolates the module's filesystem view by monkeypatching its
WEB_UI_DIR and ASSETS_DIR module globals to point at a tmp_path - none of
these tests touch the real web_ui/ or assets/ directories, and none of them
invoke a real npm/node process (subprocess.run is monkeypatched wherever a
build would otherwise be triggered).
"""

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import graphlink_frontend_bootstrap as gfb


@pytest.fixture(autouse=True)
def _clean_dev_env(monkeypatch):
    # Never let a real GRAPHLINK_FRONTEND_DEV / GRAPHLINK_FRONTEND_DEV_URL in
    # the test-runner's environment leak into these tests' expectations, and
    # reset the warn-once dedup so each test observes its own warnings.
    monkeypatch.delenv(gfb.DEV_MODE_ENV_VAR, raising=False)
    monkeypatch.delenv(gfb.DEV_SERVER_URL_ENV_VAR, raising=False)
    gfb._warned_dev_url_issues.clear()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A minimal, isolated web_ui/ + assets/ pair with one island
    ("composer"), pre-built and fresh."""
    web_ui_dir = tmp_path / "web_ui"
    assets_dir = tmp_path / "assets"
    island_src = web_ui_dir / "src" / "islands" / "composer"
    island_src.mkdir(parents=True)
    (island_src / "main.tsx").write_text("// entry", encoding="utf-8")
    (web_ui_dir / "package.json").write_text("{}", encoding="utf-8")
    (web_ui_dir / "package-lock.json").write_text("{}", encoding="utf-8")

    node_modules = web_ui_dir / "node_modules"
    node_modules.mkdir()
    (node_modules / ".package-lock.json").write_text("{}", encoding="utf-8")

    island_out = assets_dir / "composer"
    island_out.mkdir(parents=True)
    (island_out / "index.html").write_text("<html></html>", encoding="utf-8")

    # Built output and node_modules marker must be newer than every source
    # file for the "fresh" starting state tests expect.
    now = time.time()
    os.utime(island_src / "main.tsx", (now - 10, now - 10))
    os.utime(web_ui_dir / "package-lock.json", (now - 10, now - 10))
    os.utime(node_modules / ".package-lock.json", (now, now))
    os.utime(island_out / "index.html", (now, now))

    monkeypatch.setattr(gfb, "WEB_UI_DIR", web_ui_dir)
    monkeypatch.setattr(gfb, "ASSETS_DIR", assets_dir)
    return {"web_ui": web_ui_dir, "assets": assets_dir, "island_src": island_src, "island_out": island_out}


class TestDiscoverIslands:
    def test_finds_every_subdirectory_under_src_islands(self, workspace):
        (workspace["web_ui"] / "src" / "islands" / "settings").mkdir()
        assert gfb.discover_islands() == ["composer", "settings"]

    def test_empty_list_when_no_islands_directory_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gfb, "WEB_UI_DIR", tmp_path / "nonexistent_web_ui")
        assert gfb.discover_islands() == []


class TestStalenessDetection:
    def test_fresh_workspace_is_not_stale(self, workspace):
        newest = gfb._newest_source_mtime()
        assert gfb._island_is_stale("composer", newest) is False

    def test_missing_built_output_is_stale(self, workspace):
        (workspace["island_out"] / "index.html").unlink()
        newest = gfb._newest_source_mtime()
        assert gfb._island_is_stale("composer", newest) is True

    def test_newer_source_file_makes_island_stale(self, workspace):
        future = time.time() + 100
        os.utime(workspace["island_src"] / "main.tsx", (future, future))
        newest = gfb._newest_source_mtime()
        assert gfb._island_is_stale("composer", newest) is True

    def test_edited_shared_lib_file_makes_every_island_stale(self, workspace):
        # lib/ isn't island-specific - a change there affects every bundle,
        # so staleness must not be scoped to one island's own subdirectory.
        (workspace["web_ui"] / "src" / "islands" / "settings").mkdir()
        settings_out = workspace["assets"] / "settings"
        settings_out.mkdir()
        (settings_out / "index.html").write_text("<html></html>", encoding="utf-8")
        now = time.time()
        os.utime(settings_out / "index.html", (now, now))

        lib_dir = workspace["web_ui"] / "src" / "lib" / "bridge-core"
        lib_dir.mkdir(parents=True)
        lib_file = lib_dir / "transport.ts"
        lib_file.write_text("export {}", encoding="utf-8")
        future = time.time() + 100
        os.utime(lib_file, (future, future))

        newest = gfb._newest_source_mtime()
        assert gfb._island_is_stale("composer", newest) is True
        assert gfb._island_is_stale("settings", newest) is True

    def test_node_modules_and_dist_are_never_a_staleness_signal(self, workspace):
        # A change deep inside node_modules (e.g. npm touching lockfile
        # metadata) must never look like a source edit.
        junk = workspace["web_ui"] / "node_modules" / "some-package" / "index.js"
        junk.parent.mkdir(parents=True)
        junk.write_text("module.exports = {}", encoding="utf-8")
        future = time.time() + 100
        os.utime(junk, (future, future))

        newest = gfb._newest_source_mtime()
        assert gfb._island_is_stale("composer", newest) is False

    def test_test_files_are_never_a_staleness_signal(self, workspace):
        # Editing a *.test.ts(x) file doesn't change `vite build` output.
        test_file = workspace["island_src"] / "ComposerApp.test.tsx"
        test_file.write_text("// test", encoding="utf-8")
        future = time.time() + 100
        os.utime(test_file, (future, future))

        newest = gfb._newest_source_mtime()
        assert gfb._island_is_stale("composer", newest) is False

    def test_no_web_ui_source_tree_trusts_existing_built_output(self, tmp_path, monkeypatch):
        # A constrained install shipping only prebuilt assets, no web_ui/
        # source at all - nothing to compare staleness against, so trust
        # what's already built rather than attempt an impossible build.
        assets_dir = tmp_path / "assets"
        (assets_dir / "composer").mkdir(parents=True)
        (assets_dir / "composer" / "index.html").write_text("<html></html>", encoding="utf-8")
        monkeypatch.setattr(gfb, "WEB_UI_DIR", tmp_path / "nonexistent_web_ui")
        monkeypatch.setattr(gfb, "ASSETS_DIR", assets_dir)

        newest = gfb._newest_source_mtime()
        assert newest is None
        assert gfb._island_is_stale("composer", newest) is False


class TestNodeModulesNeedsInstall:
    def test_fresh_install_does_not_need_reinstall(self, workspace):
        assert gfb._node_modules_needs_install() is False

    def test_missing_install_marker_needs_install(self, workspace):
        (workspace["web_ui"] / "node_modules" / ".package-lock.json").unlink()
        assert gfb._node_modules_needs_install() is True

    def test_lockfile_newer_than_install_marker_needs_reinstall(self, workspace):
        future = time.time() + 100
        os.utime(workspace["web_ui"] / "package-lock.json", (future, future))
        assert gfb._node_modules_needs_install() is True


class TestBypassPaths:
    def test_frozen_build_never_touches_the_filesystem_check(self, workspace, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        called = {"discover": False}
        monkeypatch.setattr(gfb, "discover_islands", lambda: called.__setitem__("discover", True) or [])
        gfb.ensure_frontend_built()
        assert called["discover"] is False, "frozen bypass must return before even discovering islands"

    def test_dev_mode_env_var_skips_the_build(self, workspace, monkeypatch):
        monkeypatch.setenv(gfb.DEV_MODE_ENV_VAR, "1")
        called = {"discover": False}
        monkeypatch.setattr(gfb, "discover_islands", lambda: called.__setitem__("discover", True) or [])
        gfb.ensure_frontend_built()
        assert called["discover"] is False

    @pytest.mark.parametrize("value", ["1", "true", "True", "yes", "on"])
    def test_dev_mode_recognizes_common_truthy_spellings(self, monkeypatch, value):
        monkeypatch.setenv(gfb.DEV_MODE_ENV_VAR, value)
        assert gfb._dev_mode_requested() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "", "off"])
    def test_dev_mode_rejects_falsy_spellings(self, monkeypatch, value):
        monkeypatch.setenv(gfb.DEV_MODE_ENV_VAR, value)
        assert gfb._dev_mode_requested() is False

    def test_no_islands_discovered_is_a_silent_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gfb, "WEB_UI_DIR", tmp_path / "empty_web_ui")
        monkeypatch.setattr(gfb, "ASSETS_DIR", tmp_path / "assets")
        gfb.ensure_frontend_built()  # must not raise


class TestResolveDevServerOrigin:
    """resolve_dev_server_origin() - the trigger for the live
    dev-server-in-window path. Pure env-var/frozen logic, no QApplication,
    matching this file's existing convention."""

    def test_neither_var_set_is_none(self):
        assert gfb.resolve_dev_server_origin() is None

    def test_dev_flag_alone_is_none_and_silent(self, monkeypatch, caplog):
        # GRAPHLINK_FRONTEND_DEV alone is today's normal, intentional dev
        # loop (build-skip only) - it must stay warning-free.
        monkeypatch.setenv(gfb.DEV_MODE_ENV_VAR, "1")
        with caplog.at_level("WARNING"):
            assert gfb.resolve_dev_server_origin() is None
        assert caplog.records == []

    def test_url_alone_is_none_with_a_warning_naming_both_vars(self, monkeypatch, caplog):
        monkeypatch.setenv(gfb.DEV_SERVER_URL_ENV_VAR, "http://127.0.0.1:5173")
        with caplog.at_level("WARNING"):
            assert gfb.resolve_dev_server_origin() is None
        assert len(caplog.records) == 1
        assert gfb.DEV_SERVER_URL_ENV_VAR in caplog.text
        assert gfb.DEV_MODE_ENV_VAR in caplog.text

    def test_misconfiguration_warning_is_logged_once_not_per_call(self, monkeypatch, caplog):
        # The request interceptor re-resolves on every intercepted request;
        # an unguarded warning would repeat once per subresource.
        monkeypatch.setenv(gfb.DEV_SERVER_URL_ENV_VAR, "http://127.0.0.1:5173")
        with caplog.at_level("WARNING"):
            for _ in range(5):
                assert gfb.resolve_dev_server_origin() is None
        assert len(caplog.records) == 1

    def test_both_set_validly_returns_exactly_that_origin(self, monkeypatch):
        monkeypatch.setenv(gfb.DEV_MODE_ENV_VAR, "1")
        monkeypatch.setenv(gfb.DEV_SERVER_URL_ENV_VAR, "http://127.0.0.1:5173")
        assert gfb.resolve_dev_server_origin() == "http://127.0.0.1:5173"

    def test_localhost_hostname_is_accepted_and_preserved(self, monkeypatch):
        monkeypatch.setenv(gfb.DEV_MODE_ENV_VAR, "1")
        monkeypatch.setenv(gfb.DEV_SERVER_URL_ENV_VAR, "http://localhost:5173")
        assert gfb.resolve_dev_server_origin() == "http://localhost:5173"

    def test_a_path_suffix_is_normalized_away(self, monkeypatch):
        monkeypatch.setenv(gfb.DEV_MODE_ENV_VAR, "1")
        monkeypatch.setenv(gfb.DEV_SERVER_URL_ENV_VAR, "http://127.0.0.1:5173/index.html")
        assert gfb.resolve_dev_server_origin() == "http://127.0.0.1:5173"

    @pytest.mark.parametrize("bad_url", [
        "http://evil.example.com:5173",   # non-loopback host
        "http://192.168.1.50:5173",       # intranet host
        "https://127.0.0.1:5173",         # wrong scheme
        "ws://127.0.0.1:5173",            # wrong scheme
        "file:///C:/x",                   # wrong scheme
        "http://127.0.0.1",               # missing port - invalid, never defaulted to 80
        "http://127.0.0.1:0",             # port 0 is not a real listen port
        "not a url",                       # unparseable
        "http://127.0.0.1:notaport",      # invalid port digits
    ])
    def test_invalid_or_disallowed_urls_fail_closed_with_a_warning(self, monkeypatch, caplog, bad_url):
        monkeypatch.setenv(gfb.DEV_MODE_ENV_VAR, "1")
        monkeypatch.setenv(gfb.DEV_SERVER_URL_ENV_VAR, bad_url)
        with caplog.at_level("WARNING"):
            assert gfb.resolve_dev_server_origin() is None
        assert len(caplog.records) == 1

    def test_frozen_build_is_none_even_with_both_vars_validly_set(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setenv(gfb.DEV_MODE_ENV_VAR, "1")
        monkeypatch.setenv(gfb.DEV_SERVER_URL_ENV_VAR, "http://127.0.0.1:5173")
        assert gfb.resolve_dev_server_origin() is None


class TestNodeAndNpmDetection:
    def test_raises_actionable_error_when_node_missing(self, monkeypatch):
        monkeypatch.setattr(gfb.shutil, "which", lambda name: None)
        with pytest.raises(gfb.FrontendBootstrapError, match="Node.js and npm were not found"):
            gfb._require_node_and_npm()

    def test_raises_actionable_error_naming_npm_specifically_when_only_npm_is_missing(self, monkeypatch):
        # Node present, npm absent (a real scenario - e.g. a broken PATH) must
        # not be misreported as "Node.js was not found", which would send a
        # developer who already has a working Node install down the wrong path.
        monkeypatch.setattr(gfb.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None)
        with pytest.raises(gfb.FrontendBootstrapError, match="npm was not found") as exc_info:
            gfb._require_node_and_npm()
        assert "Node.js was not found" not in str(exc_info.value)

    def test_raises_actionable_error_naming_node_specifically_when_only_node_is_missing(self, monkeypatch):
        monkeypatch.setattr(gfb.shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
        with pytest.raises(gfb.FrontendBootstrapError, match="Node.js was not found") as exc_info:
            gfb._require_node_and_npm()
        assert "npm was not found" not in str(exc_info.value)

    def test_raises_actionable_error_when_node_too_old(self, monkeypatch):
        monkeypatch.setattr(gfb.shutil, "which", lambda name: f"/usr/bin/{name}")

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="v18.19.0\n", stderr="")

        monkeypatch.setattr(gfb.subprocess, "run", fake_run)
        with pytest.raises(gfb.FrontendBootstrapError, match=str(gfb.MIN_NODE_MAJOR)):
            gfb._require_node_and_npm()

    def test_accepts_node_at_exactly_the_minimum_major(self, monkeypatch):
        monkeypatch.setattr(gfb.shutil, "which", lambda name: f"/usr/bin/{name}")

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=f"v{gfb.MIN_NODE_MAJOR}.0.0\n", stderr="")

        monkeypatch.setattr(gfb.subprocess, "run", fake_run)
        node_path, npm_path = gfb._require_node_and_npm()
        assert node_path == "/usr/bin/node"
        assert npm_path == "/usr/bin/npm"

    def test_unparseable_version_output_is_an_actionable_error_not_a_crash(self, monkeypatch):
        monkeypatch.setattr(gfb.shutil, "which", lambda name: f"/usr/bin/{name}")

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="not a version\n", stderr="")

        monkeypatch.setattr(gfb.subprocess, "run", fake_run)
        with pytest.raises(gfb.FrontendBootstrapError, match="parse"):
            gfb._require_node_and_npm()


class TestBuildOrchestration:
    def test_stale_island_triggers_npm_ci_then_build_with_the_right_island_env(self, workspace, monkeypatch):
        future = time.time() + 100
        os.utime(workspace["island_src"] / "main.tsx", (future, future))
        # Also make node_modules stale (lockfile newer than the install
        # marker) so this test actually exercises the npm-ci-then-build
        # ordering it's named for, not just the build-only path already
        # covered by test_fresh_node_modules_skips_npm_ci_but_still_builds_stale_island.
        os.utime(workspace["web_ui"] / "package-lock.json", (future, future))

        monkeypatch.setattr(gfb.shutil, "which", lambda name: f"/usr/bin/{name}")

        def fake_node_version(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=f"v{gfb.MIN_NODE_MAJOR}.0.0\n", stderr="")

        monkeypatch.setattr(gfb.subprocess, "run", fake_node_version)

        calls = []

        def fake_run_npm(npm_path, args, *, extra_env=None, timeout_seconds=None):
            calls.append((args, extra_env))

        monkeypatch.setattr(gfb, "_run_npm", fake_run_npm)

        gfb.ensure_frontend_built()

        assert calls[0] == (["ci"], None), "npm ci must run first when node_modules is stale too"
        assert calls[1] == (["run", "build"], {"GRAPHLINK_ISLAND": "composer"})

    def test_fresh_node_modules_skips_npm_ci_but_still_builds_stale_island(self, workspace, monkeypatch):
        future = time.time() + 100
        os.utime(workspace["island_src"] / "main.tsx", (future, future))
        # node_modules install marker newer than the lockfile - no reinstall needed.
        os.utime(workspace["web_ui"] / "node_modules" / ".package-lock.json", (future + 1, future + 1))

        monkeypatch.setattr(gfb.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(
            gfb.subprocess, "run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, stdout=f"v{gfb.MIN_NODE_MAJOR}.0.0\n", stderr=""),
        )

        calls = []
        monkeypatch.setattr(gfb, "_run_npm", lambda npm_path, args, **kw: calls.append(args))

        gfb.ensure_frontend_built()

        assert calls == [["run", "build"]], "npm ci must be skipped when node_modules is already current"

    def test_fresh_workspace_never_calls_npm_at_all(self, workspace, monkeypatch):
        called = {"run_npm": False}
        monkeypatch.setattr(gfb, "_run_npm", lambda *a, **k: called.__setitem__("run_npm", True))
        gfb.ensure_frontend_built()
        assert called["run_npm"] is False

    def test_npm_failure_raises_an_actionable_error_with_command_output(self, monkeypatch):
        monkeypatch.setattr(
            gfb.subprocess, "run",
            lambda *a, **k: (_ for _ in ()).throw(
                subprocess.CalledProcessError(1, ["npm", "run", "build"], output="stdout text", stderr="stderr text")
            ),
        )
        with pytest.raises(gfb.FrontendBootstrapError, match="npm run build"):
            gfb._run_npm("npm", ["run", "build"])


class TestGraphlinkAppBootstrapFailureWiring:
    """graphlink_app.py's _handle_frontend_bootstrap_error() - split out of
    main() specifically so this is testable without constructing a real
    QApplication/ChatWindow or touching real crash/log files under
    ~/.graphlink/. Covers two real gaps an adversarial review found in the
    original inline version: a bootstrap failure must be logged (a windowed
    app has no console for an unlogged error to land in) and must not be
    mistaken for a crash on the next launch (the running.lock sentinel must
    be cleared on this controlled exit path, same as a normal clean exit)."""

    @pytest.fixture
    def graphlink_app_module(self):
        import graphlink_app as mod
        return mod

    def test_logs_shows_dialog_marks_clean_exit_and_exits_with_code_1(self, graphlink_app_module, monkeypatch):
        calls = []
        monkeypatch.setattr(
            graphlink_app_module, "logger",
            type("FakeLogger", (), {"error": staticmethod(lambda *a, **k: calls.append(("log", a, k)))})(),
        )
        monkeypatch.setattr(
            graphlink_app_module.QMessageBox, "critical",
            staticmethod(lambda *a, **k: calls.append(("dialog", a, k))),
        )
        monkeypatch.setattr(
            graphlink_app_module, "mark_clean_exit",
            lambda *a, **k: calls.append(("mark_clean_exit", a, k)),
        )

        exc = graphlink_app_module.FrontendBootstrapError("Node.js was not found on PATH.")
        with pytest.raises(SystemExit) as exit_info:
            graphlink_app_module._handle_frontend_bootstrap_error(exc)

        assert exit_info.value.code == 1
        kinds = [call[0] for call in calls]
        assert kinds == ["log", "dialog", "mark_clean_exit"], (
            "must log, then show the dialog, then clear the crash sentinel, in that order, "
            "before exiting"
        )
        assert "Node.js was not found" in str(calls[0][1])
        assert "Node.js was not found" in calls[1][1][2]  # QMessageBox.critical(parent, title, text)
