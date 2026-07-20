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

from graphlink_licensing import SettingsManager
from graphlink_settings_bridge import SettingsBridge

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
