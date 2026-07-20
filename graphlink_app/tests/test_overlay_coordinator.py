"""Unit tests for OverlayCoordinator - Phase 5 increment 1.

A plain Python class (not Qt-dependent), so these are pure unit tests against
fake hosts rather than real widgets.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_overlay_coordinator import OverlayCoordinator


def _make_host(visible=True):
    host = MagicMock()
    host.isVisible.return_value = visible
    return host


def test_reposition_all_calls_reposition_and_raise_for_visible_hosts_only():
    coordinator = OverlayCoordinator()
    visible_host = _make_host(visible=True)
    hidden_host = _make_host(visible=False)
    visible_reposition = MagicMock()
    hidden_reposition = MagicMock()
    coordinator.register(visible_host, visible_reposition, z_priority=0)
    coordinator.register(hidden_host, hidden_reposition, z_priority=1)

    coordinator.reposition_all()

    visible_reposition.assert_called_once()
    visible_host.raise_.assert_called_once()
    hidden_reposition.assert_not_called()
    hidden_host.raise_.assert_not_called()


def test_reposition_all_raises_in_ascending_z_priority_order():
    coordinator = OverlayCoordinator()
    low = _make_host()
    high = _make_host()
    order = []
    low.raise_.side_effect = lambda: order.append("low")
    high.raise_.side_effect = lambda: order.append("high")
    # Registered high-priority first, low-priority second - the coordinator
    # must still raise low before high regardless of registration order.
    coordinator.register(high, MagicMock(), z_priority=10)
    coordinator.register(low, MagicMock(), z_priority=1)

    coordinator.reposition_all()

    assert order == ["low", "high"]


def test_unregister_removes_a_host_from_future_passes():
    coordinator = OverlayCoordinator()
    host = _make_host()
    reposition = MagicMock()
    coordinator.register(host, reposition)

    coordinator.unregister(host)
    coordinator.reposition_all()

    reposition.assert_not_called()
    host.raise_.assert_not_called()


def test_unregister_a_host_never_registered_does_not_raise():
    coordinator = OverlayCoordinator()

    coordinator.unregister(_make_host())  # must not raise
