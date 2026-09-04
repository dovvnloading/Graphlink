"""No test reaches a real Ollama daemon.

conftest.py's _never_probe_a_real_ollama_daemon fixture is what stops it; this
is the assertion that the fixture is doing its job, so a later refactor that
drops it fails here instead of quietly adding a hundred TCP connects back into
every run.

Written as "no socket was opened", not "show raised", so it keeps holding if
the probing moves to a different client or a different function.
"""

from __future__ import annotations

import socket

import pytest

import api_provider


@pytest.fixture
def opened_sockets(monkeypatch):
    """Every address anything tries to connect to during the test."""
    attempts: list[object] = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def record(self, address):
        attempts.append(address)
        return real_connect(self, address)

    def record_ex(self, address):
        attempts.append(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", record)
    monkeypatch.setattr(socket.socket, "connect_ex", record_ex)
    return attempts


PROBES = [
    ("ollama_supports_tools", lambda: api_provider.ollama_supports_tools("probe-model:1b")),
    ("ollama_supports_embedding", lambda: api_provider.ollama_supports_embedding("probe-model:1b")),
    ("_ollama_effective_context_window",
     lambda: api_provider._ollama_effective_context_window("probe-model:1b")),
]


@pytest.mark.parametrize("name, probe", PROBES, ids=[n for n, _ in PROBES])
def test_the_ollama_probes_open_no_socket(name, probe, opened_sockets, monkeypatch):
    """Each of the three entry points that used to reach 127.0.0.1:11434."""
    monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {})
    monkeypatch.setattr(api_provider, "_OLLAMA_CONTEXT_WINDOW_CACHE", {})

    probe()

    assert opened_sockets == []


def test_the_probes_still_report_unavailable_rather_than_guessing(monkeypatch):
    """The stub has to produce the same answer an unreachable daemon did -
    "I don't know" - not a fabricated capability set."""
    monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {})
    monkeypatch.setattr(api_provider, "_OLLAMA_CONTEXT_WINDOW_CACHE", {})

    assert api_provider._get_ollama_capabilities("probe-model:1b") is None
    assert api_provider._get_ollama_context_window("probe-model:1b") is None
    # ...and the documented fallback still applies on top of that None.
    assert api_provider._ollama_effective_context_window("probe-model:1b") == (
        api_provider._DEFAULT_CONTEXT_WINDOW
    )


def test_a_test_can_still_supply_its_own_show(monkeypatch):
    """The fixture must not lock out the tests that legitimately fake the
    daemon - backend/tests/test_context_budget.py does exactly this."""
    monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {})

    def fake_show(model):
        return {"capabilities": ["vision"]}

    monkeypatch.setattr(api_provider.ollama, "show", fake_show)
    assert api_provider._get_ollama_capabilities("probe-model:1b") == {"vision"}
