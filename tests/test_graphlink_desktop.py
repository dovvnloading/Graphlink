"""Boot-sequence tests for graphlink_desktop.py (ADR-002 stage 2.1c).

main() has no dependency-injection seam of its own - it does real process
I/O (backend thread start, HTTP health polling, a native webview window,
crash-sentinel file writes to the real ~/.graphlink/) - so every
integration-level test here monkeypatches each external dependency at its
point of use:

- backend.crash_recovery's functions are patched on the real module object,
  since main() does a fresh `from backend.crash_recovery import (...)`
  every call - patching graphlink_desktop's own names would miss that.
- _start_backend is replaced outright with a fake returning a controllable
  fake server/thread pair, so no real uvicorn.Server or backend.app is ever
  constructed.
- _wait_for_health is replaced outright for main()-level tests (its own
  thread-liveness behavior is unit-tested directly, below, against the
  real implementation - each layer tests its own concern).
- webview is patched into sys.modules before main() reaches its own
  `import webview` - import is a sys.modules lookup, so monkeypatch.setattr
  cannot intercept a statement that has not run yet, but pre-seeding
  sys.modules works because import finds the fake already there.

These fixes were previously untested (confirmed: no test file referenced
graphlink_desktop.main()/_start_backend()/_wait_for_health() before this
one), despite each of the four fixes below changing observable behavior.
"""

from __future__ import annotations

import sys
import time
import types
from types import SimpleNamespace

import pytest

import graphlink_desktop
import backend.crash_recovery as crash_recovery_module


class _FakeThread:
    """Stand-in for threading.Thread with fully controllable liveness,
    independent of whether any real thread ever ran - the tests need to
    assert behavior for "backend thread died instantly" and "backend
    thread still running" without racing real OS threads."""

    def __init__(self, alive=True):
        self._alive = alive
        self.join_calls = []

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        self.join_calls.append(timeout)


class _FakeServer:
    def __init__(self):
        self.should_exit = False


# -- _wait_for_health: thread-liveness check (issue 1) -----------------------


def test_wait_for_health_returns_false_immediately_when_thread_is_dead(monkeypatch):
    # Regression guard: previously a backend thread that died in its first
    # millisecond (create_app()/server.run() raised immediately) was
    # indistinguishable from "still connecting", so the caller waited out
    # the entire STARTUP_TIMEOUT_SECONDS before reporting a misleading
    # "did not become healthy" - never even trying to distinguish the two.
    request_attempts = []

    def fake_urlopen(*args, **kwargs):
        request_attempts.append(args)
        raise OSError("connection refused")

    monkeypatch.setattr(graphlink_desktop.urllib.request, "urlopen", fake_urlopen)

    start = time.monotonic()
    result = graphlink_desktop._wait_for_health(
        "http://127.0.0.1:9", timeout=5.0, thread=_FakeThread(alive=False)
    )
    elapsed = time.monotonic() - start

    assert result is False
    assert elapsed < 1.0, "must fail fast on a dead thread, not wait out the full timeout"
    assert request_attempts == [], "a confirmed-dead thread must not even attempt a health request"


def test_wait_for_health_polls_normally_and_succeeds_when_thread_is_alive(monkeypatch):
    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    attempts = {"count": 0}

    def fake_urlopen(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OSError("connection refused")  # still starting up
        return _FakeResponse()  # healthy on the second poll

    monkeypatch.setattr(graphlink_desktop.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(graphlink_desktop.time, "sleep", lambda _seconds: None)

    result = graphlink_desktop._wait_for_health(
        "http://127.0.0.1:9", timeout=5.0, thread=_FakeThread(alive=True)
    )

    assert result is True
    assert attempts["count"] == 2


def test_wait_for_health_with_thread_omitted_is_a_pure_timeout_unchanged_from_before(monkeypatch):
    # thread=None (the default) must skip the liveness check entirely -
    # existing behavior for any caller that doesn't pass it.
    monkeypatch.setattr(
        graphlink_desktop.urllib.request, "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("connection refused")),
    )
    monkeypatch.setattr(graphlink_desktop.time, "sleep", lambda _seconds: None)

    result = graphlink_desktop._wait_for_health("http://127.0.0.1:9", timeout=0.05)

    assert result is False


# -- _shutdown_backend: cooperative shutdown (issue 4) ------------------------


def test_shutdown_backend_sets_should_exit_and_joins_with_a_bounded_timeout():
    server = _FakeServer()
    thread = _FakeThread(alive=True)

    graphlink_desktop._shutdown_backend(server, thread)

    assert server.should_exit is True
    assert thread.join_calls == [5.0], "join must be bounded, never unbounded"


def test_shutdown_backend_is_a_no_op_safe_call_when_the_thread_already_finished():
    server = _FakeServer()
    thread = _FakeThread(alive=False)

    graphlink_desktop._shutdown_backend(server, thread)  # must not raise

    assert server.should_exit is True
    assert thread.join_calls == [5.0]


# -- main(): integration-level tests for the composed fixes -------------------


@pytest.fixture
def desktop_harness(tmp_path, monkeypatch):
    """Wires every external dependency main() touches to a controllable
    fake and returns a namespace the test can both configure (webview
    behavior, env var) and assert against (recorded calls)."""
    state = SimpleNamespace(
        mark_running_calls=0,
        mark_clean_exit_calls=0,
        shutdown_calls=[],
        webview_create_window_calls=[],
        webview_start_calls=[],
        webview_start_side_effect=None,
        wait_for_health_result=True,
    )

    spa_index = tmp_path / "web_ui" / "dist" / "app" / "index.html"
    spa_index.parent.mkdir(parents=True)
    spa_index.write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(graphlink_desktop, "REPO_ROOT", tmp_path)

    monkeypatch.setattr(crash_recovery_module, "configure_logging", lambda *a, **k: None)
    monkeypatch.setattr(crash_recovery_module, "install_exception_handlers", lambda *a, **k: None)
    monkeypatch.setattr(crash_recovery_module, "previous_run_crashed", lambda *a, **k: False)

    def fake_mark_running(*_a, **_k):
        state.mark_running_calls += 1

    def fake_mark_clean_exit(*_a, **_k):
        state.mark_clean_exit_calls += 1

    monkeypatch.setattr(crash_recovery_module, "mark_running", fake_mark_running)
    monkeypatch.setattr(crash_recovery_module, "mark_clean_exit", fake_mark_clean_exit)

    fake_server = _FakeServer()
    fake_thread = _FakeThread(alive=True)

    def fake_start_backend(port, previous_run_crashed=False):
        return fake_server, fake_thread

    def fake_wait_for_health(base_url, timeout, thread=None):
        return state.wait_for_health_result

    def fake_shutdown_backend(server, thread):
        state.shutdown_calls.append((server, thread))

    monkeypatch.setattr(graphlink_desktop, "_start_backend", fake_start_backend)
    monkeypatch.setattr(graphlink_desktop, "_wait_for_health", fake_wait_for_health)
    monkeypatch.setattr(graphlink_desktop, "_shutdown_backend", fake_shutdown_backend)

    fake_webview = types.ModuleType("webview")

    def fake_create_window(*args, **kwargs):
        state.webview_create_window_calls.append((args, kwargs))

    def fake_start(**kwargs):
        state.webview_start_calls.append(kwargs)
        if state.webview_start_side_effect is not None:
            raise state.webview_start_side_effect

    fake_webview.create_window = fake_create_window
    fake_webview.start = fake_start
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    state.fake_server = fake_server
    state.fake_thread = fake_thread
    return state


def test_main_returns_1_when_spa_build_is_missing(desktop_harness, tmp_path, monkeypatch):
    (tmp_path / "web_ui" / "dist" / "app" / "index.html").unlink()

    result = graphlink_desktop.main()

    assert result == 1
    assert desktop_harness.mark_clean_exit_calls == 1, "even this early exit must clear the sentinel"
    assert desktop_harness.webview_create_window_calls == [], "must never reach the window"


def test_main_falls_back_to_a_free_port_when_env_var_is_not_numeric(desktop_harness, monkeypatch):
    # Regression guard: previously int(os.environ.get(...)) raised
    # ValueError AFTER mark_running() had already written the crash
    # sentinel, and the raise propagated out of main() entirely - skipping
    # mark_clean_exit() and leaving a false "previous run crashed" notice
    # on the NEXT launch.
    monkeypatch.setenv("GRAPHLINK_BACKEND_PORT", "not-a-port")

    result = graphlink_desktop.main()

    assert result == 0
    assert desktop_harness.mark_clean_exit_calls == 1
    assert len(desktop_harness.webview_create_window_calls) == 1, "must still reach the window on a bad port value"


def test_main_uses_the_pinned_port_when_env_var_is_a_valid_integer(desktop_harness, monkeypatch):
    monkeypatch.setenv("GRAPHLINK_BACKEND_PORT", "54321")

    result = graphlink_desktop.main()

    assert result == 0
    (_args, kwargs) = desktop_harness.webview_create_window_calls[0]
    assert kwargs["url"] == "http://127.0.0.1:54321"


def test_main_clears_the_crash_sentinel_even_when_webview_start_raises(desktop_harness):
    # THE regression this stage exists to fix: webview.start() raising
    # (e.g. the WebView2 runtime is missing) used to skip mark_clean_exit()
    # entirely, since it lived unconditionally after the (unguarded) call.
    desktop_harness.webview_start_side_effect = RuntimeError("WebView2 runtime not found")

    result = graphlink_desktop.main()

    assert result == 1
    assert desktop_harness.mark_clean_exit_calls == 1, "the sentinel must still clear on this failure path"


def test_main_shuts_down_the_backend_when_webview_start_raises(desktop_harness):
    desktop_harness.webview_start_side_effect = RuntimeError("WebView2 runtime not found")

    graphlink_desktop.main()

    assert desktop_harness.shutdown_calls == [(desktop_harness.fake_server, desktop_harness.fake_thread)]


def test_main_shuts_down_the_backend_gracefully_on_a_normal_window_close(desktop_harness):
    result = graphlink_desktop.main()

    assert result == 0
    assert desktop_harness.shutdown_calls == [(desktop_harness.fake_server, desktop_harness.fake_thread)]
    assert desktop_harness.mark_clean_exit_calls == 1


def test_main_shuts_down_the_backend_when_the_health_check_times_out(desktop_harness):
    # Composition of issues 1 and 4: a backend that never becomes healthy
    # (hung, or died in a way the liveness check didn't catch mid-poll)
    # must still be told to shut down rather than silently abandoned as a
    # daemon thread with the process exiting around it.
    desktop_harness.wait_for_health_result = False

    result = graphlink_desktop.main()

    assert result == 1
    assert desktop_harness.shutdown_calls == [(desktop_harness.fake_server, desktop_harness.fake_thread)]
    assert desktop_harness.mark_clean_exit_calls == 1
    assert desktop_harness.webview_create_window_calls == [], "must never reach the window if never healthy"
