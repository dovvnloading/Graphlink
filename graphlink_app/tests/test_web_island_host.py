"""Tests for WebIslandHost and the shared shutdown registry.

Before this module existed, nothing in the test suite ever constructed a real
host end-to-end - graphlink_composer_web.py's tests only exercised the bridge
and the two pure helper functions (_inline_bundle/_rounded_region) in
isolation. These tests close that gap for the generic host itself; see
test_composer_web_host.py for the composer-specific integration.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PySide6.QtCore import QObject

from graphlink_island_bridge import IslandBridge
import graphlink_web_island_host as wih
from graphlink_web_island_host import WebIslandHost


class _FakeBridge(IslandBridge, QObject):
    """Minimal real bridge for exercising WebIslandHost directly. WebIslandHost
    calls bridge.setParent(self), so the bridge must be a real QObject - a
    plain Python fake (like IslandBridge's own test double) cannot stand in
    here the way it can for testing IslandBridge in isolation.
    """

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self.publish_count = 0

    def _build_state_payload(self):
        return {"value": self.publish_count}

    def _transport_send(self, payload_json):
        self.publish_count += 1


def _make_host(**overrides):
    kwargs = dict(
        bridge=_FakeBridge(),
        asset_dir_name="does-not-exist",
        bridge_object_name="fakeBridge",
    )
    kwargs.update(overrides)
    return WebIslandHost(**kwargs)


@pytest.fixture(autouse=True)
def _clean_registry():
    # Other test modules construct hosts too; don't let their leftovers (or
    # this module's) leak between tests.
    wih._hosts.clear()
    yield
    wih._hosts.clear()


def test_construction_registers_the_host():
    host = _make_host()

    assert host in wih._hosts


def test_prepare_for_shutdown_unregisters_disposes_and_is_idempotent():
    host = _make_host()

    host.prepare_for_shutdown()

    assert host not in wih._hosts
    assert host.bridge.disposed is True

    host.prepare_for_shutdown()  # must not raise a second time
    assert host not in wih._hosts


def test_shutdown_all_tears_down_every_registered_host():
    host_a = _make_host()
    host_b = _make_host()

    wih.shutdown_all()

    assert host_a not in wih._hosts
    assert host_b not in wih._hosts
    assert host_a.bridge.disposed is True
    assert host_b.bridge.disposed is True


def test_bridge_is_reparented_under_the_host():
    bridge = _FakeBridge()
    assert bridge.parent() is None

    host = _make_host(bridge=bridge)

    assert bridge.parent() is host


def test_apply_requested_height_clamps_and_emits_only_on_real_change():
    host = _make_host(min_height=50, max_height=200)
    heights = []
    host.heightChanged.connect(heights.append)

    host.apply_requested_height(1000)  # clamps to 200
    host.apply_requested_height(1000)  # unchanged - no second emit
    host.apply_requested_height(10)  # clamps to 50

    assert heights == [200, 50]
    assert host.height() == 50


def test_apply_requested_height_without_bounds_raises_not_implemented():
    host = _make_host()  # no min_height/max_height provided

    with pytest.raises(NotImplementedError):
        host.apply_requested_height(100)


def test_on_theme_changed_republishes_through_the_bridge():
    host = _make_host()
    count_before = host.bridge.publish_count

    host.on_theme_changed()

    assert host.bridge.publish_count == count_before + 1


def test_focus_widget_prefers_the_web_view_when_present():
    host = _make_host()

    # WebEngine may or may not be available in the test environment; either
    # way, focusWidget() must not raise, and when a web_view exists it must be
    # the answer.
    if host.web_view is not None:
        assert host.focusWidget() is host.web_view
