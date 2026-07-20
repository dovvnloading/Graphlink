"""The settings bridge's secrets invariant: no stored key/token ever appears
in any snapshot string, across a full lifecycle.

Extends test_secrets_at_rest.py's proven pattern - assert the literal
secret string is absent from every serialized form - to a NEW serialization
surface that test never covered: SettingsBridge.publish()'s outbound JSON,
rather than session.dat. IslandBridge.publish() re-serializes the FULL
snapshot on every mutation, unconditionally, which is exactly why this
invariant needs its own proof: a naive payload builder including the raw
token (even "to answer is a key configured") would leak it into the wire
format on literally every subsequent, unrelated field change on the page.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

import api_provider
import graphlink_config as config
from graphlink_licensing import SettingsManager
from graphlink_settings_bridge import API_TASKS, SettingsBridge

_SECRET = "ghp_do-not-leak-this-1234567890"


def _all_snapshots(bridge: SettingsBridge) -> list[str]:
    snapshots: list[str] = []
    bridge.stateChanged.connect(snapshots.append)
    return snapshots


class TestGithubTokenNeverCrossesTheBridge:
    def test_opened_with_a_token_already_configured(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        settings_manager.set_github_token(_SECRET)
        bridge = SettingsBridge(settings_manager)
        snapshots = _all_snapshots(bridge)

        bridge.ready()

        assert snapshots, "ready() did not publish anything"
        assert _SECRET not in snapshots[-1]
        assert json.loads(snapshots[-1])["githubTokenConfigured"] is True

    def test_across_a_full_set_and_clear_lifecycle(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)
        snapshots = _all_snapshots(bridge)

        bridge.ready()  # opened, no token configured
        bridge.setGithubToken(_SECRET)  # mid-edit -> saved
        bridge.setActiveSection("Integrations")  # an unrelated field change
        bridge.clearGithubToken()  # cleared

        assert len(snapshots) == 4
        for snapshot in snapshots:
            assert _SECRET not in snapshot, (
                f"the literal secret string appeared in a published snapshot: {snapshot!r}"
            )

    def test_a_different_secret_value_also_never_leaks(self, tmp_path):
        # Not just the one fixture string - a second, differently-shaped
        # token must be caught too, so this isn't accidentally coupled to
        # _SECRET's specific characters.
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)
        snapshots = _all_snapshots(bridge)
        other_secret = "github_pat_11ABCDEFG0123456789_zzzzzzzzzzzzzzzzzzzzzz"

        bridge.setGithubToken(other_secret)

        assert other_secret not in snapshots[-1]

    def test_configured_flag_tracks_presence_not_value(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)
        snapshots = _all_snapshots(bridge)

        bridge.setGithubToken(_SECRET)
        first_configured_payload = json.loads(snapshots[-1])

        bridge.setGithubToken("a-completely-different-token-value")
        second_configured_payload = json.loads(snapshots[-1])

        # Same boolean shape both times - the wire contract never
        # distinguishes one configured token from another.
        assert first_configured_payload["githubTokenConfigured"] is True
        assert second_configured_payload["githubTokenConfigured"] is True
        assert set(first_configured_payload.keys()) == set(second_configured_payload.keys())


_API_SECRET = "sk-do-not-leak-this-api-key-0987654321"


def _valid_config_json(provider: str, api_key: str) -> str:
    tasks = [t for t in API_TASKS if not (provider == config.API_PROVIDER_ANTHROPIC and t == config.TASK_IMAGE_GEN)]
    return json.dumps({
        "provider": provider,
        "baseUrl": "https://api.openai.com/v1" if provider == config.API_PROVIDER_OPENAI else "",
        "apiKey": api_key,
        "taskModels": {task: "some-model" for task in tasks},
    })


class TestApiKeysNeverCrossTheBridge:
    def test_a_successful_save_never_leaks_the_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: None)
        for var in ("GRAPHLINK_API_PROVIDER", "GRAPHLINK_OPENAI_API_KEY", "GRAPHLINK_API_BASE"):
            monkeypatch.delenv(var, raising=False)
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)
        snapshots = _all_snapshots(bridge)

        bridge.saveApiConfiguration(_valid_config_json(config.API_PROVIDER_OPENAI, _API_SECRET))

        assert snapshots
        for snapshot in snapshots:
            assert _API_SECRET not in snapshot
        assert json.loads(snapshots[-1])["openaiKeyConfigured"] is True

    def test_a_rejected_save_never_leaks_the_key_either(self, tmp_path, monkeypatch):
        # The exact scenario a naive "echo the notice with the key in it"
        # implementation would get wrong - a failed init still must not put
        # the attempted key anywhere in the published payload.
        def _raise(*_args, **_kwargs):
            raise RuntimeError(f"rejected key {_API_SECRET}")

        monkeypatch.setattr(api_provider, "initialize_api", _raise)
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)
        snapshots = _all_snapshots(bridge)

        bridge.saveApiConfiguration(_valid_config_json(config.API_PROVIDER_OPENAI, _API_SECRET))

        assert snapshots
        assert _API_SECRET not in snapshots[-1]
        assert json.loads(snapshots[-1])["openaiKeyConfigured"] is False

    def test_a_failed_load_worker_never_leaks_the_key_either(self, tmp_path, monkeypatch):
        # A second, distinct code path with the same risk:
        # ApiModelLoadWorker.run() also calls initialize_api() with the raw
        # key, and its own exception text could echo it back the same way.
        def _raise(*_args, **_kwargs):
            raise RuntimeError(f"rejected key {_API_SECRET}")

        monkeypatch.setattr(api_provider, "initialize_api", _raise)
        bridge = SettingsBridge(SettingsManager(tmp_path / "session.dat"))
        snapshots = _all_snapshots(bridge)

        bridge.loadAvailableModels(_API_SECRET)
        worker = bridge._api_worker
        if worker is not None:
            worker.wait(2000)
        QApplication.processEvents()

        assert snapshots
        for snapshot in snapshots:
            assert _API_SECRET not in snapshot

    def test_across_a_full_provider_switch_lifecycle(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: None)
        for var in (
            "GRAPHLINK_API_PROVIDER",
            "GRAPHLINK_OPENAI_API_KEY",
            "GRAPHLINK_API_BASE",
            "GRAPHLINK_ANTHROPIC_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)
        snapshots = _all_snapshots(bridge)
        anthropic_secret = "sk-ant-do-not-leak-this-either"

        bridge.ready()
        bridge.saveApiConfiguration(_valid_config_json(config.API_PROVIDER_OPENAI, _API_SECRET))
        bridge.setApiProvider(config.API_PROVIDER_ANTHROPIC)
        bridge.saveApiConfiguration(_valid_config_json(config.API_PROVIDER_ANTHROPIC, anthropic_secret))

        for snapshot in snapshots:
            assert _API_SECRET not in snapshot
            assert anthropic_secret not in snapshot
