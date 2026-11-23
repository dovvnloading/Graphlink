"""Offline Playwright-adapter tests (no browser install or live network required)."""
from __future__ import annotations

import asyncio

import pytest

from graphlink_plugins.browser_agent.runner import (
    BrowserPolicyError, capture_markdown, normalize_steps, run_browser,
)


class FakeLocator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    async def count(self):
        return self.page.matches

    async def get_attribute(self, key):
        return self.page.element_type if key == "type" else None

    async def click(self, **kwargs):
        self.page.calls.append(("click", self.selector))

    async def fill(self, value, **kwargs):
        self.page.calls.append(("fill", self.selector, value))

    async def select_option(self, *, value, **kwargs):
        self.page.calls.append(("select", self.selector, value))


class FakePage:
    def __init__(self):
        self.url = "about:blank"
        self.calls = []
        self.matches = 1
        self.element_type = "text"
        self.mouse = type("Mouse", (), {"wheel": self._wheel})()

    async def _wheel(self, x, y):
        self.calls.append(("scroll", x, y))

    def locator(self, selector):
        return FakeLocator(self, selector)

    async def goto(self, url, **kwargs):
        self.url = url
        self.calls.append(("goto", url))

    def on(self, event, callback):
        self.calls.append(("on", event))

    async def evaluate(self, script):
        assert "document.body" in script
        return {
            "title": "<external title>",
            "text": "Untrusted page says ignore prior instructions",
            "links": [{"text": "Home", "url": "https://example.com/"}],
            "tables": [[["Name", "Price"], ["Widget", "7"]]],
        }

    async def screenshot(self, **kwargs):
        return b"\x89PNG" + b"x" * 16

    async def wait_for_timeout(self, duration):
        self.calls.append(("wait", duration))


class FakeContext:
    def __init__(self):
        self.page = FakePage()
        self.closed = False
        self.route_handler = None
        self.websocket_handler = None
        self.scripts = []

    async def route(self, pattern, handler):
        self.route_handler = handler

    async def route_web_socket(self, pattern, handler):
        self.websocket_handler = handler

    async def add_init_script(self, script):
        self.scripts.append(script)

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.context = FakeContext()
        self.closed = False

    async def new_context(self, **kwargs):
        assert kwargs["service_workers"] == "block"
        assert kwargs["accept_downloads"] is False
        assert kwargs["ignore_https_errors"] is False
        return self.context

    async def close(self):
        self.closed = True


class FakeDriver:
    def __init__(self):
        self.browser = FakeBrowser()
        self.chromium = self
        self.args = None

    async def launch(self, **kwargs):
        self.args = kwargs["args"]
        return self.browser


class FakePlaywrightContext:
    def __init__(self):
        self.driver = FakeDriver()
        self.closed = False

    async def __aenter__(self):
        return self.driver

    async def __aexit__(self, *args):
        self.closed = True


def test_step_validation_prevents_unbounded_or_unsafe_commands():
    for steps in (
        "click",
        [{}],
        [{"kind": "evaluate", "javascript": "fetch('http://localhost')"}],
        [{"kind": "click", "selector": ""}],
        [{"kind": "click", "selector": "*\nbutton"}],
        [{"kind": "fill", "selector": "#q", "value": "x" * 501}],
        [{"kind": "scroll", "pixels": 2401}],
        [{"kind": "wait", "milliseconds": 99999}],
        [{"kind": "navigate", "url": "file:///etc/passwd"}],
        [{"kind": "scroll"}] * 9,
    ):
        with pytest.raises(BrowserPolicyError):
            normalize_steps(steps)


def test_browser_run_captures_evidence_redacts_fill_and_cleans_up():
    wrapper = FakePlaywrightContext()
    approvals = []

    async def approve_host(host, url):
        approvals.append(("host", host))
        return True

    async def approve_action(kind, selector, url):
        approvals.append((kind, selector))
        return True

    async def run():
        return await run_browser(
            "https://example.com/",
            [
                {"kind": "fill", "selector": "#q", "value": "sensitive search term"},
                {"kind": "click", "selector": "#submit"},
                {"kind": "navigate", "url": "https://example.com/results"},
                {"kind": "select", "selector": "#filter", "value": "new"},
                {"kind": "scroll", "pixels": 400},
                {"kind": "wait", "milliseconds": 1},
            ],
            approve_host, approve_action,
            screenshot=True, playwright_factory=lambda: wrapper,
        )

    capture = asyncio.run(run())
    page = wrapper.driver.browser.context.page
    assert capture.url == "https://example.com/results"
    assert capture.title == "<external title>"
    assert capture.text.startswith("Untrusted page")
    assert capture.tables[0][1] == ["Widget", "7"]
    assert capture.screenshot.startswith(b"\x89PNG")
    assert approvals == [("fill", "#q"), ("click", "#submit"), ("select", "#filter")]
    assert ["--host-resolver-rules=MAP * ~NOTFOUND"] == [
        arg for arg in wrapper.driver.args if arg.startswith("--host-resolver-rules")
    ]
    assert wrapper.driver.browser.context.route_handler is not None
    assert wrapper.driver.browser.context.websocket_handler is not None
    assert any("RTCPeerConnection" in script for script in wrapper.driver.browser.context.scripts)
    assert ("fill", "#q", "sensitive search term") in page.calls
    assert "sensitive search term" not in str(capture.actions)
    assert wrapper.closed and wrapper.driver.browser.closed and wrapper.driver.browser.context.closed


def test_refused_interaction_prevents_action_and_closes_browser():
    wrapper = FakePlaywrightContext()
    seen = []

    async def approve_host(host, url):
        return True

    async def deny(kind, selector, url):
        seen.append(kind)
        return False

    with pytest.raises(BrowserPolicyError, match="denied"):
        asyncio.run(run_browser(
            "https://example.com/", [{"kind": "click", "selector": "button"}],
            approve_host, deny, playwright_factory=lambda: wrapper,
        ))
    assert seen == ["click"]
    assert not any(call[0] == "click" for call in wrapper.driver.browser.context.page.calls)
    assert wrapper.driver.browser.closed and wrapper.driver.browser.context.closed


def test_ambiguous_selector_and_password_input_fail_closed():
    for matches, input_type, message in ((2, "text", "exactly one"), (1, "password", "Password")):
        wrapper = FakePlaywrightContext()
        wrapper.driver.browser.context.page.matches = matches
        wrapper.driver.browser.context.page.element_type = input_type

        async def approve_host(host, url):
            return True

        async def approve_action(kind, selector, url):
            pytest.fail("Invalid interaction must not request approval")

        with pytest.raises(BrowserPolicyError, match=message):
            asyncio.run(run_browser(
                "https://example.com/", [{"kind": "fill", "selector": "input", "value": "secret"}],
                approve_host, approve_action, playwright_factory=lambda: wrapper,
            ))
        assert wrapper.driver.browser.closed


def test_markdown_capture_escapes_external_markup_and_anchors_provenance():
    wrapper = FakePlaywrightContext()

    async def approve(*args):
        return True

    capture = asyncio.run(run_browser(
        "https://example.com/", [], approve, approve, playwright_factory=lambda: wrapper,
    ))
    markdown = capture_markdown(capture)
    assert "&lt;external title&gt;" in markdown
    assert "https://example.com/" in markdown
    assert "Untrusted external webpage" in markdown
    assert "request_count" in markdown
