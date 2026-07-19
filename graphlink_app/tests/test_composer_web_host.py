"""Integration tests for ComposerWebHost - the composer-specific WebIslandHost
subclass. Nothing previously constructed a real ComposerWebHost end-to-end;
test_composer_bridge.py only exercised the bridge and the pure helper
functions in isolation. These tests close that gap.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from PySide6.QtWidgets import QPushButton

import graphlink_frontend_bootstrap as gfb
import graphlink_webengine
from graphlink_composer import ComposerController
from graphlink_composer_bridge import COMPOSER_MAX_HEIGHT, COMPOSER_MIN_HEIGHT
from graphlink_composer_web import ComposerWebHost
import graphlink_web_island_host as wih


@pytest.fixture(autouse=True)
def _force_offline_unless_test_opts_in(monkeypatch):
    # Without this, a developer running the suite with both live-dev env vars
    # exported (this feature's own target workflow) would silently construct
    # every host below in LIVE mode - pointing real QWebEngineViews at a dev
    # server and swapping which profile they use. Clear both vars so the
    # default for every test here is deterministically the offline branch;
    # the two branch-selection tests opt back in by monkeypatching
    # resolve_dev_server_origin directly.
    monkeypatch.delenv(gfb.DEV_MODE_ENV_VAR, raising=False)
    monkeypatch.delenv(gfb.DEV_SERVER_URL_ENV_VAR, raising=False)


class _Window:
    settings_manager = None
    current_node = None
    pending_attachments = []
    composer_controller = None


def _make_host(window=None, controller=None):
    return ComposerWebHost(window or _Window(), controller or ComposerController(), None)


def _dev_scripts(host):
    return host.web_view.page().scripts().find("qwebchannel-bootstrap")


def _clear_registry():
    wih._hosts.clear()


def test_construction_registers_with_the_shared_shutdown_registry():
    _clear_registry()
    host = _make_host()

    assert host in wih._hosts


def test_legacy_compat_buttons_exist_hidden_and_real():
    host = _make_host()

    assert isinstance(host.attach_file_btn, QPushButton)
    assert isinstance(host.send_button, QPushButton)
    assert host.attach_file_btn.isVisible() is False
    assert host.send_button.isVisible() is False


def test_legacy_signals_exist_and_are_connectable():
    host = _make_host()

    # ChatWindow.__init__ connects to all of these; none is ever emitted by
    # ComposerWebHost. This only proves they exist and accept a connection -
    # deleting them is Phase 2 scope, not something to assert against here.
    for name in (
        "sendRequested",
        "textChanged",
        "attachRequested",
        "filesDropped",
        "textDropped",
        "attachmentRemoved",
        "largePasteDetected",
        "composerHeightChanged",
    ):
        getattr(host, name).connect(lambda *args: None)


def test_text_editing_methods_round_trip_through_the_bridge():
    host = _make_host()

    host.setText("hello")
    assert host.text() == "hello"

    host.insertPlainText(" world")
    assert host.text() == "hello world"

    host.clear()
    assert host.text() == ""


def test_height_negotiation_chain_is_fully_wired_end_to_end():
    """bridge.resize() (the JS-facing intent) -> heightRequested ->
    apply_requested_height -> heightChanged -> composerHeightChanged (the
    legacy signal ChatWindow._sync_footer_height listens to)."""
    host = _make_host()
    heights = []
    host.composerHeightChanged.connect(heights.append)

    host.bridge.resize(1)
    host.bridge.resize(10_000)

    # The host is already at COMPOSER_MIN_HEIGHT from construction, so the
    # first resize-to-minimum request is a no-op (matches pre-existing
    # behavior of the original ComposerWebHost, unchanged by this refactor).
    assert heights == [COMPOSER_MAX_HEIGHT]
    assert host.height() == COMPOSER_MAX_HEIGHT

    host.bridge.resize(200)
    assert heights == [COMPOSER_MAX_HEIGHT, 200]


def test_legacy_provider_and_request_state_compat_methods_do_not_raise():
    host = _make_host()

    host.set_context_items([])
    host.set_context_anchor(None)
    host.set_provider_status("Ollama")
    host.set_request_state(active=True)
    host.set_editor_enabled(False)


def test_on_theme_changed_is_inherited_and_republishes():
    host = _make_host()
    states = []
    host.bridge.stateChanged.connect(states.append)
    count_before = len(states)

    host.on_theme_changed()

    assert len(states) == count_before + 1


def test_prepare_for_shutdown_disposes_bridge_hides_web_view_and_unregisters():
    _clear_registry()
    host = _make_host()

    host.prepare_for_shutdown()

    assert host not in wih._hosts
    assert host.bridge.disposed is True
    if host.web_view is not None:
        assert host.web_view.isVisible() is False

    host.prepare_for_shutdown()  # idempotent


def test_registry_shutdown_all_tears_down_every_composer_host():
    _clear_registry()
    host_a = _make_host()
    host_b = _make_host()

    wih.shutdown_all()

    assert host_a not in wih._hosts
    assert host_b not in wih._hosts
    assert host_a.bridge.disposed is True
    assert host_b.bridge.disposed is True


class TestLiveDevServerBranchSelection:
    """The host's actual load-mode decision - the one thing neither the pure
    resolve_dev_server_origin tests nor the pure interceptor tests exercise.
    Proves a frozen/no-env build takes the offline branch (offline profile, no
    injected script) and a live build takes the dev branch (dedicated dev
    profile + injected qwebchannel bootstrap)."""

    def test_offline_branch_uses_the_offline_profile_and_injects_no_script(self, monkeypatch):
        # The default with both env vars cleared; also the exact shape a frozen
        # build hits (resolve_dev_server_origin returns None when frozen).
        monkeypatch.setattr(wih, "resolve_dev_server_origin", lambda: None)
        host = _make_host()
        if host.web_view is None:
            pytest.skip("WebEngine unavailable in this environment")

        assert host._dev_origin is None
        assert host.web_view.page().profile() is graphlink_webengine._get_offline_profile()
        assert _dev_scripts(host) == []

    def test_live_branch_uses_the_dev_profile_and_injects_the_qwebchannel_bootstrap(self, monkeypatch):
        monkeypatch.setattr(wih, "resolve_dev_server_origin", lambda: "http://127.0.0.1:5173")
        host = _make_host()
        if host.web_view is None:
            pytest.skip("WebEngine unavailable in this environment")

        assert host._dev_origin == "http://127.0.0.1:5173"
        assert host.web_view.page().profile() is graphlink_webengine._get_dev_server_profile()
        # Exactly one qwebchannel bootstrap script, on the PAGE's collection...
        assert len(_dev_scripts(host)) == 1
        # ...and never on the shared profile (the isolation invariant the
        # injection-script docstring leans on - a future move onto the profile
        # would hand the untrusted HTML-renderer preview the same bootstrap).
        assert graphlink_webengine._get_dev_server_profile().scripts().find("qwebchannel-bootstrap") == []

    def test_offline_and_live_branches_use_genuinely_different_profiles(self, monkeypatch):
        monkeypatch.setattr(wih, "resolve_dev_server_origin", lambda: None)
        offline_host = _make_host()
        monkeypatch.setattr(wih, "resolve_dev_server_origin", lambda: "http://127.0.0.1:5173")
        live_host = _make_host()
        if offline_host.web_view is None or live_host.web_view is None:
            pytest.skip("WebEngine unavailable in this environment")

        assert offline_host.web_view.page().profile() is not live_host.web_view.page().profile()
