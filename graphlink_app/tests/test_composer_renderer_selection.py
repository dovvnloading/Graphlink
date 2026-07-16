"""Composer renderer selection contract tests."""

from graphlink_window import _composer_renderer_from_env


def test_react_composer_is_the_default(monkeypatch):
    monkeypatch.delenv("GRAPHLINK_COMPOSER_RENDERER", raising=False)

    assert _composer_renderer_from_env() == "web"


def test_legacy_composer_requires_explicit_opt_out(monkeypatch):
    monkeypatch.setenv("GRAPHLINK_COMPOSER_RENDERER", "legacy")

    assert _composer_renderer_from_env() == "legacy"


def test_unknown_renderer_values_do_not_downgrade_to_legacy(monkeypatch):
    monkeypatch.setenv("GRAPHLINK_COMPOSER_RENDERER", "stale-launcher-value")

    assert _composer_renderer_from_env() == "web"
