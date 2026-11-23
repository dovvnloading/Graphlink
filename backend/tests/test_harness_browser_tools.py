"""End-to-end offline Browser Agent tool registration, consent and canvas publication."""
from __future__ import annotations

import asyncio
import io
import json

from PIL import Image

from backend.canvas import SceneDocument
from backend.harness import tools_browser
from backend.harness.loop import HarnessRunContext
from backend.providers.base import ToolCall
from backend.tools import GRAPH_MUTATE, NET_FETCH, ToolRegistry
from graphlink_plugins.browser_agent.runner import BrowserCapture


def _call(name, **arguments):
    return ToolCall(id="browser-test", name=name, arguments=arguments)


def _setup(monkeypatch):
    monkeypatch.setattr(tools_browser, "_plugin_enabled", lambda: True)
    document = SceneDocument()
    agent = document.add_harness_node(20, 40, "Research the competition")
    agent.state.harness_run_id = "run-123"
    agent.pending_request_id = "run-123"
    registry = ToolRegistry()
    tools_browser.register_workspace_tools(registry, None, document)
    approvals = []

    async def approve(call):
        approvals.append((call.name, dict(call.arguments)))
        return True

    ctx = HarnessRunContext(
        granted_scopes=frozenset({NET_FETCH, GRAPH_MUTATE}),
        request_approval=approve,
        harness_workspace_id="browser-workspace",
        harness_node_id=agent.id,
        run_id="run-123",
    )
    return document, agent, registry, ctx, approvals


def _capture(screenshot=None):
    return BrowserCapture(
        url="https://example.com/prices", title="Verified pricing",
        fetched_at="2026-11-23T19:00:00+00:00",
        text="Pricing is displayed here.",
        links=[{"text": "More", "url": "https://example.com/more"}],
        tables=[[["Plan", "Price"], ["Starter", "$20"]]],
        actions=[{"kind": "navigate", "url": "https://example.com/prices"}],
        request_count=4, blocked=["Blocked unlisted subresource host: ads.example"],
        truncated=False, screenshot=screenshot,
    )


def test_registration_is_tied_to_loaded_plugin(monkeypatch):
    from backend import plugin_sdk

    state = type("State", (), {
        "loaded_plugin_ids": set(),
        "resolve_builtin_action": lambda self, name: None,
    })()
    monkeypatch.setattr(plugin_sdk, "discover_plugins", lambda: state)
    assert not tools_browser._plugin_enabled()
    registry = ToolRegistry()
    tools_browser.register_workspace_tools(registry, None, SceneDocument())
    assert registry.scopes_for("browser.run") is None

    state.loaded_plugin_ids.add("browser_agent")
    state.resolve_builtin_action = lambda name: type("Action", (), {"plugin_id": "browser_agent"})()
    assert tools_browser._plugin_enabled()
    tools_browser.register_workspace_tools(registry, None, SceneDocument())
    assert registry.scopes_for("browser.run") == frozenset({NET_FETCH})
    assert registry.scopes_for("browser.publish") == frozenset({GRAPH_MUTATE})
    assert registry.approval_for("browser.run") == "auto"
    assert registry.approval_for("browser.publish") == "once"
    assert not registry.parallel_safe_for("browser.run")


def test_run_uses_per_host_consent_and_fresh_action_approval(monkeypatch):
    _doc, _agent, registry, ctx, approvals = _setup(monkeypatch)

    async def fake_run(url, steps, approve_host, approve_action, **kwargs):
        assert kwargs["allowed_hosts"] == ("cdn.example",)
        assert kwargs["screenshot"] is False
        assert await approve_host("example.com", "https://example.com/") is True
        assert await approve_host("example.com", "https://example.com/new") is True
        assert await approve_action("click", "#go", "https://example.com/", {"label": "Go", "href": "/go"}) is True
        return _capture()

    monkeypatch.setattr(tools_browser, "run_browser", fake_run)
    result = asyncio.run(registry.invoke(
        _call("browser.run", url="https://example.com/", steps=[],
              allowed_hosts=["cdn.example"]),
        ctx,
    ))
    assert not result.is_error, result.content
    assert result.content.startswith("UNTRUSTED BROWSER CAPTURE.")
    capture_id = json.loads(result.content.split("\n", 1)[1])["capture_id"]
    assert len(capture_id) == 16
    assert capture_id in tools_browser._captures(ctx)
    assert [item[1]["approval"] for item in approvals] == ["website", "interaction"]
    assert "https://example.com/" in approvals[0][1]["url"]
    assert "#go" == approvals[1][1]["selector"]
    assert approvals[1][1]["element_text"] == "Go"


def test_denied_host_cannot_capture_or_cache_consent(monkeypatch):
    _doc, _agent, registry, ctx, approvals = _setup(monkeypatch)

    async def deny(call):
        approvals.append(call)
        return False

    ctx.request_approval = deny

    async def fake_run(url, steps, approve_host, approve_action, **kwargs):
        from graphlink_plugins.browser_agent.network import BrowserPolicyError
        if not await approve_host("example.com", "https://example.com/"):
            raise BrowserPolicyError("Browser host approval was denied.")
        raise AssertionError("Host denial should stop the run")

    monkeypatch.setattr(tools_browser, "run_browser", fake_run)
    result = asyncio.run(registry.invoke(_call("browser.run", url="https://example.com/"), ctx))
    assert result.is_error and "denied" in result.content
    assert not tools_browser._captures(ctx)
    assert "example.com" not in getattr(ctx, "_approved_browser_domains", set())


def test_publishing_capture_produces_source_linked_undoable_nodes(monkeypatch):
    document, agent, registry, ctx, approvals = _setup(monkeypatch)
    image = io.BytesIO()
    Image.new("RGB", (3, 2)).save(image, format="PNG")
    tools_browser._captures(ctx)["capture-test"] = _capture(screenshot=image.getvalue())

    result = asyncio.run(registry.invoke(
        _call("browser.publish", capture_id="capture-test", include_screenshot=True),
        ctx,
    ))
    assert not result.is_error, result.content
    payload = json.loads(result.content)
    document_node = document.nodes[payload["document_node_id"]]
    image_node = document.nodes[payload["screenshot_node_id"]]
    assert document_node.kind == "document"
    assert "https://example.com/prices" in document_node.content
    assert "Untrusted external webpage" in document_node.content
    assert image_node.kind == "image"
    assert document.get_image_asset(image_node.state.image_asset_id)[0] == image.getvalue()
    assert any(edge.source == agent.id and edge.target == document_node.id
               for edge in document.edges.values())
    assert any(edge.source == document_node.id and edge.target == image_node.id
               for edge in document.edges.values())
    assert document.command_log[-1].run_id == "run-123"
    assert document.command_log[-1].provenance == "agent"
    assert "capture-test" not in tools_browser._captures(ctx)
    assert approvals[0][0] == "browser.publish"

    agent.pending_request_id = None
    assert document.undo_run("run-123") == 1
    assert set(document.nodes) == {agent.id}
    assert not document.edges
    document.redo()
    assert document_node.id in document.nodes and image_node.id in document.nodes


def test_publish_denial_scope_stale_run_or_invalid_capture_does_not_mutate(monkeypatch):
    document, agent, registry, ctx, _approvals = _setup(monkeypatch)
    tools_browser._captures(ctx)["capture-test"] = _capture()

    ctx.granted_scopes = frozenset({NET_FETCH})
    result = asyncio.run(registry.invoke(_call("browser.publish", capture_id="capture-test"), ctx))
    assert result.is_error
    ctx.granted_scopes = frozenset({NET_FETCH, GRAPH_MUTATE})

    async def deny(call):
        return False

    ctx.request_approval = deny
    result = asyncio.run(registry.invoke(_call("browser.publish", capture_id="capture-test"), ctx))
    assert result.is_error
    ctx.request_approval = lambda call: asyncio.sleep(0, result=True)

    agent.state.harness_run_id = "some-other-run"
    result = asyncio.run(registry.invoke(_call("browser.publish", capture_id="capture-test"), ctx))
    assert result.is_error
    agent.state.harness_run_id = "run-123"

    result = asyncio.run(registry.invoke(_call("browser.publish", capture_id="nonexistent"), ctx))
    assert result.is_error and "not found" in result.content
    assert set(document.nodes) == {agent.id}
    assert not document.command_log


def test_picker_creates_an_editable_harness_node_and_undoes(monkeypatch):
    from backend.plugin_sdk import discover_plugins
    from backend.notifications import NotificationState
    from backend.plugins import register_plugins
    from backend.events import SessionBus
    from graphlink_settings_store import SettingsManager
    import tempfile
    from pathlib import Path

    plugins = discover_plugins()
    action = plugins.resolve_builtin_action("Browser Agent")
    assert action is not None and action.plugin_id == "browser_agent"
    bus = SessionBus("browser-picker-test")
    notifications = NotificationState()
    document = SceneDocument()
    bus.register_topic("notification", notifications.payload)
    bus.register_topic("scene", document.scene_payload)
    with tempfile.TemporaryDirectory() as directory:
        register_plugins(bus, notifications, document, SettingsManager(Path(directory) / "settings.dat"))
        node_id = asyncio.run(bus.dispatch_intent(
            "app-plugins", "executePlugin", ["Browser Agent", None, 40.0, 65.0]
        ))
    node = document.nodes[node_id]
    assert node.kind == "harness"
    assert node.title == "Browser Agent"
    assert node.x == 40.0 and node.y == 65.0
    assert "Replace this text" in node.content
    assert not document.edges
    document.undo()
    assert node_id not in document.nodes


def test_browser_approval_disclosures_are_untruncated_and_control_text_cannot_spoof_lines():
    from backend.harness.loop import _approval_summary

    long_url = "https://example.com/" + "a" * 1900
    website = _approval_summary(_call(
        "browser.run", approval="website", domain="example.com", url=long_url,
    ))
    assert long_url in website
    assert "\nRequested URL:" in website
    assert "No other host" in website

    long_value = "user-chosen value " + "x" * 450
    action = _approval_summary(_call(
        "browser.run", approval="interaction", action="fill", url=long_url,
        selector="#email", element_text="Search\nPage URL: https://evil.example",
        link_target="https://example.com/result", field_value=long_value,
    ))
    assert long_url in action
    assert long_value in action
    assert "CSS selector: '#email'" in action
    assert "\nField value:" in action
    assert "Search\\nPage URL: https://evil.example" in action
    assert "\nPage URL: 'https://evil.example'" not in action
