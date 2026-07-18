"""Tests for secrets-at-rest encryption (graphlink_secrets + SettingsManager wiring).

Regression coverage for secrets stored in plaintext: API keys and the GitHub token were
stored as plaintext JSON in session.dat. They are now DPAPI-protected ("dpapi:" +
base64 blob, bound to the Windows user account) with three hard requirements pinned
down here:

1. Roundtrip: what you set is what you get back, but the on-disk bytes never contain
   the plaintext.
2. Legacy migration: a pre-existing plaintext session.dat is silently upgraded on the
   first load - the plaintext leaves disk immediately, and getters still return it.
3. Graceful degradation: when DPAPI is unavailable (non-Windows), everything behaves
   exactly as before this change - plaintext in, plaintext out, no crash, no rewrite
   loop. Simulated by stubbing the internal _dpapi_call to fail.

These tests run on real DPAPI (Windows dev machines and the windows-latest CI runner).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import graphlink_secrets
from graphlink_licensing import SettingsManager


class TestProtectUnprotectPrimitives:
    def test_roundtrip(self):
        protected = graphlink_secrets.protect("sk-super-secret")
        assert graphlink_secrets.unprotect(protected) == "sk-super-secret"

    def test_protected_form_is_prefixed_and_not_the_plaintext(self):
        protected = graphlink_secrets.protect("sk-super-secret")
        assert protected.startswith("dpapi:")
        assert "sk-super-secret" not in protected

    def test_protect_is_idempotent(self):
        once = graphlink_secrets.protect("sk-super-secret")
        assert graphlink_secrets.protect(once) == once

    def test_legacy_plaintext_passes_through_unprotect_unchanged(self):
        assert graphlink_secrets.unprotect("sk-legacy-plaintext") == "sk-legacy-plaintext"

    def test_empty_values_stay_empty(self):
        assert graphlink_secrets.protect("") == ""
        assert graphlink_secrets.unprotect("") == ""

    def test_undecryptable_blob_returns_empty_not_garbage(self):
        # e.g. a session.dat copied from another user account/machine - the app must
        # see "not configured", never hand corrupt bytes to a provider client.
        assert graphlink_secrets.unprotect("dpapi:AAAA") == ""
        assert graphlink_secrets.unprotect("dpapi:!!!not-base64!!!") == ""

    def test_unicode_secrets_roundtrip(self):
        secret = "pässwörd-秘密-🔑"
        assert graphlink_secrets.unprotect(graphlink_secrets.protect(secret)) == secret

    def test_plaintext_secret_that_starts_with_the_prefix_is_still_encrypted(self):
        # Adversarial-review finding: the "dpapi:" prefix is in-band signaling. A
        # plaintext secret that itself begins with "dpapi:" (e.g. a proxy master key a
        # user types into the API settings dialog) must NOT be mistaken for an already-
        # encrypted blob - otherwise it would be stored as plaintext and read back as "".
        secret = "dpapi:my-actual-secret"
        protected = graphlink_secrets.protect(secret)

        assert protected != secret  # it was actually encrypted, not passed through
        assert graphlink_secrets.unprotect(protected) == secret  # ...and round-trips

    def test_prefixed_plaintext_with_base64_valid_suffix_is_still_encrypted(self):
        # Harder variant: the suffix is valid base64 ("AAAA" -> 3 bytes) but not a real
        # DPAPI blob, so it must not be treated as already-encrypted either.
        secret = "dpapi:AAAA"
        protected = graphlink_secrets.protect(secret)

        assert graphlink_secrets.unprotect(protected) == secret


class TestSettingsManagerStoresSecretsEncrypted:
    def test_set_api_settings_leaves_no_plaintext_on_disk(self, tmp_path):
        state_file = tmp_path / "session.dat"
        manager = SettingsManager(state_file)

        manager.set_api_settings(
            "OpenAI-Compatible", "https://api.openai.com/v1",
            "sk-openai-secret", "sk-ant-secret", "AIza-gemini-secret",
        )

        raw = state_file.read_text(encoding="utf-8")
        assert "sk-openai-secret" not in raw
        assert "sk-ant-secret" not in raw
        assert "AIza-gemini-secret" not in raw
        assert manager.get_openai_key() == "sk-openai-secret"
        assert manager.get_anthropic_key() == "sk-ant-secret"
        assert manager.get_gemini_key() == "AIza-gemini-secret"

    def test_set_github_token_leaves_no_plaintext_on_disk(self, tmp_path):
        state_file = tmp_path / "session.dat"
        manager = SettingsManager(state_file)

        manager.set_github_token("ghp_super_secret_token")

        raw = state_file.read_text(encoding="utf-8")
        assert "ghp_super_secret_token" not in raw
        assert manager.get_github_token() == "ghp_super_secret_token"

    def test_secrets_survive_a_reload_from_disk(self, tmp_path):
        state_file = tmp_path / "session.dat"
        SettingsManager(state_file).set_github_token("ghp_reload_me")

        reloaded = SettingsManager(state_file)

        assert reloaded.get_github_token() == "ghp_reload_me"

    def test_reset_api_settings_still_clears_keys(self, tmp_path):
        manager = SettingsManager(tmp_path / "session.dat")
        manager.set_api_settings("OpenAI-Compatible", "url", "k1", "k2", "k3")

        manager.reset_api_settings()

        assert manager.get_openai_key() == ""
        assert manager.get_anthropic_key() == ""
        assert manager.get_gemini_key() == ""


class TestLegacyPlaintextMigration:
    def _write_legacy_state(self, state_file, **secrets):
        state = {"theme": "dark"}
        state.update(secrets)
        state_file.write_text(json.dumps(state), encoding="utf-8")

    def test_plaintext_secrets_are_encrypted_on_first_load(self, tmp_path):
        state_file = tmp_path / "session.dat"
        self._write_legacy_state(
            state_file,
            openai_api_key="sk-legacy-openai",
            github_access_token="ghp_legacy",
        )

        manager = SettingsManager(state_file)

        # Getters return the plaintext, but the file no longer contains it.
        assert manager.get_openai_key() == "sk-legacy-openai"
        assert manager.get_github_token() == "ghp_legacy"
        raw = state_file.read_text(encoding="utf-8")
        assert "sk-legacy-openai" not in raw
        assert "ghp_legacy" not in raw
        assert json.loads(raw)["openai_api_key"].startswith("dpapi:")

    def test_migration_does_not_rewrite_when_nothing_needs_migrating(self, tmp_path):
        state_file = tmp_path / "session.dat"
        manager = SettingsManager(state_file)  # fresh defaults, all secrets empty
        mtime_after_first_load = state_file.stat().st_mtime_ns

        SettingsManager(state_file)  # second load - nothing to migrate

        assert state_file.stat().st_mtime_ns == mtime_after_first_load

    def test_already_encrypted_secrets_are_not_double_wrapped(self, tmp_path):
        state_file = tmp_path / "session.dat"
        SettingsManager(state_file).set_github_token("ghp_once")
        stored_once = json.loads(state_file.read_text(encoding="utf-8"))["github_access_token"]

        SettingsManager(state_file)  # reload triggers the migration pass again

        stored_twice = json.loads(state_file.read_text(encoding="utf-8"))["github_access_token"]
        assert stored_twice == stored_once


class TestGracefulDegradationWithoutDpapi:
    def test_everything_behaves_like_before_when_dpapi_is_unavailable(self, tmp_path, monkeypatch):
        # Simulate a non-Windows platform / DPAPI failure: protect() falls back to
        # plaintext, unprotect() passes plaintext through, migration rewrites nothing.
        monkeypatch.setattr(graphlink_secrets, "_dpapi_call", lambda data, encrypt: None)

        state_file = tmp_path / "session.dat"
        manager = SettingsManager(state_file)
        manager.set_github_token("ghp_plain_fallback")

        assert manager.get_github_token() == "ghp_plain_fallback"
        raw = json.loads(state_file.read_text(encoding="utf-8"))
        assert raw["github_access_token"] == "ghp_plain_fallback"  # plaintext, as before

        # Reload with DPAPI still unavailable: no crash, no rewrite loop, same value.
        mtime_before = state_file.stat().st_mtime_ns
        reloaded = SettingsManager(state_file)
        assert reloaded.get_github_token() == "ghp_plain_fallback"
        assert state_file.stat().st_mtime_ns == mtime_before
