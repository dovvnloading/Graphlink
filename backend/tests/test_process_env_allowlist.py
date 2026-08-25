"""Tests for graphlink_process_env's explicit-allowlist subprocess
environment builder (ADR-002 P0).

Regression coverage for the confirmed gap: the shared PythonREPL's
subprocess (graphlink_plugins/common/python_repl.py) and the Virtual
Environment Runner's venv-create/pip-install/script-run trio used to call
subprocess.Popen(...) with no `env=` argument, silently
inheriting the backend's FULL environment - including any provider API key
configured as an environment variable. safe_subprocess_env() replaces that
with an explicit allowlist; these tests pin down that it (1) actually
excludes secret-shaped variables, (2) still includes what a real
python/pip/venv subprocess needs, and (3) is case-insensitive on Windows,
where os.environ key casing is inconsistent.

Live end-to-end proof that a real `python -m venv` + `pip install` still
works under this restricted environment was done manually against this
change (real venv creation, real network pip install, confirmed the
allowlist doesn't break either) - not re-asserted here as an automated
test, since spinning up a real venv + network install in the pytest suite
would be slow and flaky in CI; this file covers the allowlist's own pure
logic instead.
"""

import os

from graphlink_process_env import safe_subprocess_env


def test_excludes_provider_api_key_style_variables(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    monkeypatch.setenv("GEMINI_API_KEY", "should-not-leak")
    monkeypatch.setenv("GRAPHLINK_ANTHROPIC_API_KEY", "should-not-leak")
    monkeypatch.setenv("GRAPHITE_OPENAI_API_KEY", "should-not-leak")

    env = safe_subprocess_env()

    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "GEMINI_API_KEY" not in env
    assert "GRAPHLINK_ANTHROPIC_API_KEY" not in env
    assert "GRAPHITE_OPENAI_API_KEY" not in env


def test_excludes_an_arbitrary_unknown_secret_shaped_variable(monkeypatch):
    """The allowlist protects even secrets this codebase has never heard
    of - a personal AWS key, a work VPN token, anything - since it copies
    through only a fixed known-safe set, not everything except a blocklist."""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "should-not-leak")
    monkeypatch.setenv("MY_RANDOM_PERSONAL_TOKEN", "should-not-leak")

    env = safe_subprocess_env()

    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "MY_RANDOM_PERSONAL_TOKEN" not in env


def test_includes_what_a_python_pip_venv_subprocess_actually_needs(monkeypatch):
    monkeypatch.setenv("PATH", r"C:\Windows\System32;C:\Python")
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("TEMP", r"C:\Users\test\AppData\Local\Temp")

    env = safe_subprocess_env()

    assert env["PATH"] == r"C:\Windows\System32;C:\Python"
    assert env["SYSTEMROOT"] == r"C:\Windows"
    assert env["TEMP"] == r"C:\Users\test\AppData\Local\Temp"


def test_case_insensitive_on_the_allowlist_match(monkeypatch):
    """Windows env var casing in os.environ is inconsistent (e.g.
    "SystemRoot" vs "SYSTEMROOT" depending on how the process was launched) -
    the allowlist match must not silently drop a needed variable just
    because of casing."""
    monkeypatch.delenv("SystemRoot", raising=False)
    monkeypatch.delenv("SYSTEMROOT", raising=False)
    monkeypatch.setenv("SystemRoot", r"C:\Windows")

    env = safe_subprocess_env()

    assert any(name.upper() == "SYSTEMROOT" for name in env)


def test_does_not_mutate_the_real_os_environ(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")

    safe_subprocess_env()

    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-should-not-leak", (
        "building a restricted copy must never touch the real process environment"
    )


def test_excludes_proxy_variables_by_design(monkeypatch):
    """Deliberately conservative (see graphlink_process_env's own module
    doc): a proxy URL can itself carry embedded credentials
    (http://user:pass@host:port), so proxy variables are excluded even
    though this could break `pip install` behind a corporate proxy."""
    monkeypatch.setenv("HTTP_PROXY", "http://user:pass@proxy.example:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pass@proxy.example:8080")

    env = safe_subprocess_env()

    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env
