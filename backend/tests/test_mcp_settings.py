"""HTTP server configuration is persistent, identity-safe and write-only."""

from __future__ import annotations

import json
import base64

import pytest

from backend.api.intents_settings_general import _mcp_servers_from_wire
from backend.settings import _mcp_servers_for_wire
from graphlink_settings_store import SettingsManager


@pytest.fixture
def manager(tmp_path):
    return SettingsManager(tmp_path / "session.dat")


def test_http_credentials_use_platform_protection_and_are_absent_from_wire(manager, monkeypatch):
    # Exercise the protection seam on all OSes. Actual Windows DPAPI and
    # non-Windows file permissions have their own required platform suites.
    import graphlink_secrets
    protected = []

    def protect(value):
        protected.append(value)
        return "test-protected:" + base64.b64encode(value.encode()).decode()

    def unprotect(value):
        if value.startswith("test-protected:"):
            return base64.b64decode(value.removeprefix("test-protected:")).decode()
        return value

    monkeypatch.setattr(graphlink_secrets, "protect", protect)
    monkeypatch.setattr(graphlink_secrets, "unprotect", unprotect)
    manager.set_mcp_servers([{
        "name": "Remote", "transport": "streamable_http", "url": "https://mcp.example.org/v1",
        "headers": {"Authorization": "Bearer confidential", "X-Api-Key": "private-header"},
    }])
    stored = manager.get_mcp_servers()[0]
    assert stored["id"]
    assert stored["command"] == ""
    assert stored["headers"]["Authorization"] == "Bearer confidential"
    wire = _mcp_servers_for_wire(manager)
    assert wire[0]["transport"] == "streamable_http"
    assert wire[0]["url"] == "https://mcp.example.org/v1"
    assert wire[0]["headerKeys"] == ["Authorization", "X-Api-Key"]
    assert "headers" not in wire[0]
    assert "confidential" not in json.dumps(wire)
    assert "private-header" not in json.dumps(wire)
    disk = manager.state_file.read_text(encoding="utf-8")
    assert "confidential" not in disk
    assert "private-header" not in disk
    assert "Bearer confidential" in protected
    assert "private-header" in protected
    reloaded = SettingsManager(manager.state_file)
    assert reloaded.get_mcp_servers() == [stored]


def test_same_name_servers_keep_distinct_credentials_on_reorder_and_toggle(manager):
    manager.set_mcp_servers([
        {"name": "Same", "transport": "streamable_http", "url": "https://one.example.org/mcp", "headers": {"X-Key": "one"}},
        {"name": "Same", "transport": "streamable_http", "url": "https://two.example.org/mcp", "headers": {"X-Key": "two"}},
    ])
    wire = list(reversed(_mcp_servers_for_wire(manager)))
    wire[0]["enabled"] = False
    manager.set_mcp_servers(_mcp_servers_from_wire(wire))
    assert [entry["headers"]["X-Key"] for entry in manager.get_mcp_servers()] == ["two", "one"]
    assert manager.get_mcp_servers()[0]["enabled"] is False


def test_http_origin_change_does_not_forward_old_credentials(manager):
    manager.set_mcp_servers([{
        "name": "Remote", "transport": "streamable_http", "url": "https://one.example.org/mcp",
        "headers": {"Authorization": "Bearer original"},
    }])
    wire = _mcp_servers_for_wire(manager)
    wire[0]["url"] = "https://two.example.org/mcp"
    manager.set_mcp_servers(_mcp_servers_from_wire(wire))
    assert manager.get_mcp_servers()[0]["headers"] == {}


def test_same_origin_edit_preserves_auth_but_explicit_empty_clears_it(manager):
    manager.set_mcp_servers([{
        "name": "Remote", "transport": "streamable_http", "url": "https://one.example.org/mcp",
        "headers": {"Authorization": "Bearer original"},
    }])
    wire = _mcp_servers_for_wire(manager)
    wire[0]["url"] = "https://one.example.org:443/mcp/new-path"
    manager.set_mcp_servers(_mcp_servers_from_wire(wire))
    assert manager.get_mcp_servers()[0]["headers"] == {"Authorization": "Bearer original"}
    wire[0]["headers"] = {}
    manager.set_mcp_servers(_mcp_servers_from_wire(wire))
    assert manager.get_mcp_servers()[0]["headers"] == {}


def test_new_origin_can_receive_explicit_replacement_credentials(manager):
    manager.set_mcp_servers([{
        "name": "Remote", "transport": "streamable_http", "url": "https://one.example.org/mcp",
        "headers": {"Authorization": "Bearer original"},
    }])
    wire = _mcp_servers_for_wire(manager)
    wire[0].update(url="https://two.example.org/mcp", headers={"Authorization": "Bearer replacement"})
    manager.set_mcp_servers(_mcp_servers_from_wire(wire))
    assert manager.get_mcp_servers()[0]["headers"] == {"Authorization": "Bearer replacement"}


def test_http_config_does_not_require_a_stdio_command_and_invalid_entries_are_isolated(manager):
    manager.set_mcp_servers([
        {"name": "remote", "transport": "streamable_http", "url": "https://example.org/mcp"},
        {"name": "missing endpoint", "transport": "streamable_http"},
        {"name": "missing command", "transport": "stdio"},
        {"name": "bad transport", "transport": "unknown", "command": "test"},
        {"name": "local", "command": "test"},
    ])
    assert [entry["name"] for entry in manager.get_mcp_servers()] == ["remote", "local"]


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), -1, 0, 301, "not a number", []])
def test_invalid_timeouts_normalize_without_breaking_settings(manager, timeout):
    manager.set_mcp_servers([{"name": "test", "command": "test", "timeout": timeout}])
    assert manager.get_mcp_servers()[0]["timeout"] == 30.0


def test_malformed_legacy_fields_are_normalized_on_read(manager):
    manager.state["mcp_servers"] = [{"name": "test", "command": "test", "args": 7,
                                      "scopes": 1, "enabled_tools": True, "timeout": "oops", "approval": "oops"}]
    entry = manager.get_mcp_servers()[0]
    assert entry["args"] == []
    assert entry["scopes"] == []
    assert entry["enabled_tools"] == []
    assert entry["timeout"] == 30.0
    assert entry["approval"] == "always"
    assert entry["id"] == manager.get_mcp_servers()[0]["id"]


def test_duplicate_identifiers_are_repaired_without_copying_secrets(manager):
    manager.set_mcp_servers([{"id": "same", "name": "original", "command": "test", "env": {"KEY": "original"}}])
    manager.set_mcp_servers([
        {"id": "same", "name": "original", "command": "test"},
        {"id": "same", "name": "copy", "command": "test"},
    ])
    original, duplicate = manager.get_mcp_servers()
    assert original["id"] != duplicate["id"]
    assert original["env"] == {"KEY": "original"}
    assert duplicate["env"] == {}


@pytest.mark.parametrize("url", [
    "https://example.org/mcp?token=do-not-publish",
    "https://example.org/mcp?api_key=do-not-publish&project_ref=public-project",
    "https://example.org/mcp?API%5FKEY=do-not-publish",
    "https://example.org/mcp?api%255fkey=do-not-publish",
    "https://example.org/mcp?X-Amz-Signature=do-not-publish",
    "https://username:do-not-publish@example.org/mcp",
])
def test_url_credentials_are_rejected_before_any_settings_mutation(manager, url):
    import asyncio
    from backend.events import IntentValidationError, SessionBus
    from backend.settings import register_settings

    manager.set_mcp_servers([{"name": "Existing", "command": "keep-me"}])
    before = manager.get_mcp_servers()
    bus = SessionBus("url-credential-validation")
    register_settings(bus, manager)
    with pytest.raises(IntentValidationError, match="authentication headers") as caught:
        asyncio.run(bus.dispatch_intent("app-settings", "setMcpServers", [[
            {"name": "Remote", "transport": "streamable_http", "url": url},
        ]]))
    assert "do-not-publish" not in str(caught.value)
    assert manager.get_mcp_servers() == before
    assert "do-not-publish" not in manager.state_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("url", [
    "https://example.org/mcp?token=do-not-publish&project_ref=visible",
    "https://do-not-publish:password@example.org/mcp",
    "https://example.org/mcp#access_token=do-not-publish",
    "https://example.org:do-not-publish/mcp",
    "malformed do-not-publish",
])
def test_legacy_url_credentials_are_redacted_without_removing_connection(manager, url):
    # Raw import / older persistence can bypass the modern WS validator.
    manager.state["mcp_servers"] = [{
        "id": "legacy-remote", "name": "Legacy remote", "transport": "streamable_http", "url": url,
    }]
    wire = _mcp_servers_for_wire(manager)
    assert len(wire) == 1 and wire[0]["id"] == "legacy-remote"
    assert "do-not-publish" not in json.dumps(wire)
    assert "redacted" in wire[0]["url"] or "invalid endpoint" in wire[0]["url"]
    assert manager.get_mcp_servers()[0]["url"] == url


def test_nonsecret_query_parameters_round_trip_without_changes(manager):
    url = "https://example.org/mcp?project_ref=abc&read_only=true&features=database%2Cdocs"
    normalized = _mcp_servers_from_wire([{"name": "Remote", "transport": "streamable_http", "url": url}])
    manager.set_mcp_servers(normalized)
    assert manager.get_mcp_servers()[0]["url"] == url
    assert _mcp_servers_for_wire(manager)[0]["url"] == url


def test_builder_mcp_startup_error_does_not_log_remote_secrets(manager, monkeypatch, caplog):
    from backend.agents import AgentDispatcher
    from backend.mcp_client import McpError
    from backend.tools import ToolRegistry
    import backend.mcp_client as client_module

    manager.set_mcp_servers([{"id": "safe-id", "name": "Remote", "command": "test",
                              "env": {"API_KEY": "do-not-log-credential"}}])
    class Client:
        def connect(self):
            raise McpError("stderr contains do-not-log-credential")
        def close(self):
            pass

    monkeypatch.setattr(client_module, "create_mcp_client", lambda _config: Client())
    dispatcher = AgentDispatcher(manager)
    with caplog.at_level("WARNING"):
        dispatcher._register_configured_mcp_tools(ToolRegistry())
    assert "MCP server unavailable" in caplog.text
    assert "do-not-log-credential" not in caplog.text
    record = next(record for record in caplog.records if record.message == "MCP server unavailable")
    assert record.mcp_server_id == "safe-id"
    assert record.error_type == "McpError"
