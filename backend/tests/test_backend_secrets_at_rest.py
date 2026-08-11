"""Tests for secrets-at-rest encryption (graphlink_secrets + SettingsManager wiring).

Ported from graphlink_app/tests/test_secrets_at_rest.py (Qt-removal plan
R7.4a) - 100% Qt-free already (confirmed by import grep: only json, pathlib,
graphlink_secrets, graphlink_settings_store.SettingsManager), it just needed a
new home now that the R7.4a API-provider settings page makes this the
active, exercised secrets path rather than a deferred one. Named
test_backend_* (not test_secrets_at_rest.py) because the legacy file of
that exact name still exists in graphlink_app/tests/ until the R7.6
cutover - two same-basename modules under different, __init__.py-less
test directories collide in pytest's default import mode, the same
collision test_backend_composer.py was already named to avoid.

Regression coverage for secrets stored in plaintext: API keys and the GitHub
token were stored as plaintext JSON in session.dat. They are now
DPAPI-protected ("dpapi:" + base64 blob, bound to the Windows user account)
with three hard requirements pinned down here:

1. Roundtrip: what you set is what you get back, but the on-disk bytes never
   contain the plaintext.
2. Legacy migration: a pre-existing plaintext session.dat is silently
   upgraded on the first load - the plaintext leaves disk immediately, and
   getters still return it.
3. Graceful degradation: when DPAPI is unavailable (non-Windows), everything
   behaves exactly as before this change - plaintext in, plaintext out, no
   crash, no rewrite loop. Simulated by stubbing the internal _dpapi_call to
   fail.

These tests run on real DPAPI (Windows dev machines and the windows-latest
CI runner).
"""

import base64
import json
import os
import stat
import sys

import pytest

import graphlink_secrets
from graphlink_settings_store import SettingsManager


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
        SettingsManager(state_file)  # fresh defaults, all secrets empty
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

    @staticmethod
    def _corrupt_a_real_blob(secret: str) -> str:
        """A real DPAPI blob (genuinely encrypted, then had a byte flipped) -
        stands in for a session.dat migrated from a different Windows
        account, or one damaged on disk. Still has the "dpapi:" prefix and
        still decodes as valid base64 (so it passes graphlink_secrets'
        cheap structural checks), but CryptUnprotectData can no longer
        decrypt it - the exact shape that used to fool migration's old
        "try to decrypt to decide if it's already encrypted" heuristic."""
        real_blob = graphlink_secrets.protect(secret)
        prefix, encoded = real_blob.split(":", 1)
        raw_bytes = bytearray(base64.b64decode(encoded))
        raw_bytes[0] ^= 0xFF
        return f"{prefix}:{base64.b64encode(bytes(raw_bytes)).decode('ascii')}"

    def test_a_foreign_or_corrupted_blob_is_left_alone_by_migration_not_double_wrapped(self, tmp_path):
        # Regression for an adversarial-review finding: migration used to
        # call protect() unconditionally on every stored secret, and
        # protect()'s own idempotency check tries to DECRYPT the value to
        # decide "already encrypted, leave alone". For a blob this account
        # genuinely can't decrypt, that attempt fails, so the OLD code
        # concluded it must be plaintext and RE-ENCRYPTED (double-wrapped)
        # it under this account's key - turning the documented, tested
        # "not configured" (unprotect() returning "" for an undecryptable
        # blob) into a silently garbage "configured" key instead.
        corrupted_blob = self._corrupt_a_real_blob("sk-genuine-secret")
        assert graphlink_secrets.unprotect(corrupted_blob) == ""  # sanity: genuinely undecryptable

        state_file = tmp_path / "session.dat"
        self._write_legacy_state(state_file, openai_api_key=corrupted_blob)

        manager = SettingsManager(state_file)

        assert manager.get_openai_key() == ""  # clean "not configured" - not garbage
        raw = json.loads(state_file.read_text(encoding="utf-8"))
        assert raw["openai_api_key"] == corrupted_blob  # untouched on disk - no double-wrap

    def test_a_foreign_or_corrupted_blob_stays_untouched_across_repeated_launches(self, tmp_path):
        # Not a byte-for-byte file comparison - _load_state fills in
        # unrelated missing default fields on this legacy-shaped fixture
        # regardless of secrets, so the file IS rewritten for THAT reason.
        # What must stay constant across every launch is the stored secret
        # value itself: still the original corrupted blob, never
        # re-wrapped, never replaced with "".
        corrupted_blob = self._corrupt_a_real_blob("sk-genuine-secret")
        state_file = tmp_path / "session.dat"
        self._write_legacy_state(state_file, github_access_token=corrupted_blob)

        SettingsManager(state_file)
        first_stored = json.loads(state_file.read_text(encoding="utf-8"))["github_access_token"]
        manager = SettingsManager(state_file)  # a second, then third launch
        second_stored = json.loads(state_file.read_text(encoding="utf-8"))["github_access_token"]

        assert first_stored == second_stored == corrupted_blob
        assert manager.get_github_token() == ""


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


class TestDpapiAvailableProbe:
    """ADR-004 stage 4.4: dpapi_available() closes audit finding H12 - before
    this, a DPAPI failure was observable only as an absence (no "dpapi:"
    prefix ever appearing on disk), never as a signal a user could see. It
    must verify a REAL round-trip, not just that the encrypt call returned
    something non-None."""

    def test_reflects_real_dpapi_state_with_no_monkeypatching(self):
        # Matches this file's own module docstring: these tests run on real
        # DPAPI (Windows dev machines and the windows-latest CI runner).
        assert graphlink_secrets.dpapi_available() is True

    def test_true_when_the_probe_round_trips(self, monkeypatch):
        monkeypatch.setattr(graphlink_secrets, "_dpapi_call", lambda data, encrypt: data)
        assert graphlink_secrets.dpapi_available() is True

    def test_false_when_the_encrypt_call_fails(self, monkeypatch):
        monkeypatch.setattr(graphlink_secrets, "_dpapi_call", lambda data, encrypt: None if encrypt else data)
        assert graphlink_secrets.dpapi_available() is False

    def test_false_when_the_decrypt_call_fails(self, monkeypatch):
        monkeypatch.setattr(
            graphlink_secrets,
            "_dpapi_call",
            lambda data, encrypt: b"encrypted-blob" if encrypt else None,
        )
        assert graphlink_secrets.dpapi_available() is False

    def test_false_when_the_round_trip_returns_a_mismatched_value(self, monkeypatch):
        # A pathological case where CryptUnprotectData "succeeds" (non-None)
        # but returns something other than the original probe -
        # dpapi_available must verify the value itself, not just presence.
        monkeypatch.setattr(
            graphlink_secrets,
            "_dpapi_call",
            lambda data, encrypt: b"encrypted-blob" if encrypt else b"not-the-probe",
        )
        assert graphlink_secrets.dpapi_available() is False


class TestSecretsEncryptedAtRestFlag:
    """ADR-004 stage 4.4: SettingsManager.secrets_encrypted_at_rest() is what
    backend/settings.py's wire payload surfaces as secretsEncryptedAtRest,
    rendered as a persistent Settings UI badge when False."""

    def test_true_when_dpapi_is_available(self, tmp_path):
        manager = SettingsManager(tmp_path / "session.dat")
        assert manager.secrets_encrypted_at_rest() is True

    def test_false_when_dpapi_is_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(graphlink_secrets, "_dpapi_call", lambda data, encrypt: None)
        manager = SettingsManager(tmp_path / "session.dat")
        assert manager.secrets_encrypted_at_rest() is False

    def test_flag_is_not_recomputed_merely_by_reading_it(self, tmp_path, monkeypatch):
        manager = SettingsManager(tmp_path / "session.dat")
        assert manager.secrets_encrypted_at_rest() is True

        # DPAPI "goes down" mid-process (e.g. a group policy change) - just
        # calling secrets_encrypted_at_rest() again must NOT re-probe or
        # flip it; it only updates from a REAL secret-mutating call (see
        # TestSecretsEncryptedAtRestTracksRealOutcomes below).
        monkeypatch.setattr(graphlink_secrets, "_dpapi_call", lambda data, encrypt: None)

        assert manager.secrets_encrypted_at_rest() is True


class TestSecretsEncryptedAtRestTracksRealOutcomes:
    """ADR-004 stage 4.4 (adversarial-review fix): an adversarial review
    found the flag could silently diverge from reality - cached True while
    a real save actually fell back to plaintext (DPAPI failing between
    construction and that save), or stuck False even after DPAPI recovered
    and a real save succeeded. _protect_and_track closes this by updating
    the flag from the REAL outcome of every secret-mutating call, not just
    the one-time construction probe."""

    @staticmethod
    def _toggle(monkeypatch, up=True):
        # Routes through the REAL _dpapi_call (genuine WinAPI round-trip,
        # matching this file's own convention of running on real DPAPI)
        # when "up", and simulates a failure by returning None when not -
        # this lets a single test move DPAPI from "working" to "failing"
        # (or back) mid-test, which a single monkeypatch.setattr can't.
        state = {"up": up}
        real_call = graphlink_secrets._dpapi_call

        def toggling_call(data, encrypt):
            if not state["up"]:
                return None
            return real_call(data, encrypt)

        monkeypatch.setattr(graphlink_secrets, "_dpapi_call", toggling_call)
        return state

    def test_a_real_save_that_falls_back_to_plaintext_flips_the_flag_to_false(self, tmp_path, monkeypatch):
        dpapi = self._toggle(monkeypatch, up=True)
        state_file = tmp_path / "session.dat"
        manager = SettingsManager(state_file)
        assert manager.secrets_encrypted_at_rest() is True

        dpapi["up"] = False  # DPAPI transiently fails between construction and this save
        manager.set_api_settings("OpenAI-Compatible", "https://x/v1", "sk-real-secret", "", "")

        assert manager.secrets_encrypted_at_rest() is False
        raw = json.loads(state_file.read_text(encoding="utf-8"))
        assert raw["openai_api_key"] == "sk-real-secret"  # plaintext on disk, matching the flag

    def test_a_real_save_that_succeeds_after_recovery_flips_the_flag_to_true(self, tmp_path, monkeypatch):
        dpapi = self._toggle(monkeypatch, up=False)
        state_file = tmp_path / "session.dat"
        manager = SettingsManager(state_file)
        assert manager.secrets_encrypted_at_rest() is False

        dpapi["up"] = True  # DPAPI recovers before this save
        manager.set_api_settings("OpenAI-Compatible", "https://x/v1", "sk-real-secret", "", "")

        assert manager.secrets_encrypted_at_rest() is True
        raw = json.loads(state_file.read_text(encoding="utf-8"))
        assert raw["openai_api_key"].startswith("dpapi:")

    def test_saving_an_empty_secret_does_not_change_the_flag_either_way(self, tmp_path, monkeypatch):
        dpapi = self._toggle(monkeypatch, up=True)
        manager = SettingsManager(tmp_path / "session.dat")
        assert manager.secrets_encrypted_at_rest() is True

        dpapi["up"] = False
        manager.set_github_token("")  # nothing real was protected - no evidence either way

        assert manager.secrets_encrypted_at_rest() is True

    def test_set_github_token_also_tracks_the_flag_not_just_set_api_settings(self, tmp_path, monkeypatch):
        dpapi = self._toggle(monkeypatch, up=True)
        manager = SettingsManager(tmp_path / "session.dat")

        dpapi["up"] = False
        manager.set_github_token("ghp_real_token")

        assert manager.secrets_encrypted_at_rest() is False

    def test_migrating_a_legacy_plaintext_secret_reflects_the_real_encrypt_outcome(self, tmp_path, monkeypatch):
        state_file = tmp_path / "session.dat"
        state_file.write_text(json.dumps({"openai_api_key": "sk-legacy-plaintext"}), encoding="utf-8")
        self._toggle(monkeypatch, up=True)

        manager = SettingsManager(state_file)

        assert manager.secrets_encrypted_at_rest() is True
        assert manager.get_openai_key() == "sk-legacy-plaintext"

    def test_migrating_an_already_encrypted_secret_while_dpapi_is_down_reports_false_not_a_stale_true(
        self, tmp_path, monkeypatch
    ):
        # Regression for a bug found in an EARLIER version of this fix
        # (caught during manual browser verification, not the automated
        # review): _protect_and_track originally inferred the outcome from
        # whether `protected` still looked like a "dpapi:"-prefixed value.
        # _migrate_plaintext_secrets feeds the ALREADY-STORED (possibly
        # already-encrypted) value from disk into _protect_and_track on
        # every launch - and protect() on an already-encrypted secret that
        # fails to re-verify falls back to returning that input UNCHANGED,
        # which still carries the "dpapi:" prefix from before. A
        # shape-based check kept reporting encrypted==True on this exact
        # path even while DPAPI was fully down. Re-probing via
        # dpapi_available() instead of inspecting the output's shape closes
        # this.
        state_file = tmp_path / "session.dat"
        SettingsManager(state_file).set_github_token("ghp_already_encrypted")
        raw = json.loads(state_file.read_text(encoding="utf-8"))
        assert raw["github_access_token"].startswith("dpapi:")  # real DPAPI on this box - genuinely encrypted

        monkeypatch.setattr(graphlink_secrets, "_dpapi_call", lambda data, encrypt: None)
        manager = SettingsManager(state_file)  # fresh launch; migration re-touches the stored value

        assert manager.secrets_encrypted_at_rest() is False


class TestSessionFilePermissionsAreRestricted:
    """ADR-004 stage 4.4: session.dat holds the encrypted-or-plaintext API
    keys/GitHub token, so it gets POSIX 0600 on every launch (self-heal) and
    on every save (the atomic-write temp file). chmod's real effect is
    POSIX-only (see graphlink_settings_store.py's own __init__ comment on
    why Windows os.chmod only toggles the read-only attribute, not real
    per-owner permission bits) - so the platform-independent assertion here
    is "chmod(path, 0o600) was actually invoked", with a POSIX-only
    bit-for-bit check layered on top where it's meaningful."""

    def test_chmod_is_invoked_with_0600_on_the_real_state_file(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(os, "chmod", lambda path, mode: calls.append((path, mode)))

        state_file = tmp_path / "session.dat"
        SettingsManager(state_file)

        assert (state_file, 0o600) in calls

    def test_posix_permission_bits_are_actually_0600(self, tmp_path):
        if sys.platform == "win32":
            pytest.skip("chmod is a no-op on Windows - see class docstring")

        state_file = tmp_path / "session.dat"
        SettingsManager(state_file)

        assert stat.S_IMODE(state_file.stat().st_mode) == 0o600

    def test_self_heals_a_pre_existing_file_with_looser_permissions(self, tmp_path):
        if sys.platform == "win32":
            pytest.skip("chmod is a no-op on Windows - see class docstring")

        state_file = tmp_path / "session.dat"
        state_file.write_text("{}", encoding="utf-8")
        os.chmod(state_file, 0o644)

        SettingsManager(state_file)

        assert stat.S_IMODE(state_file.stat().st_mode) == 0o600

    def test_a_chmod_failure_does_not_crash_construction(self, tmp_path, monkeypatch):
        def _boom(path, mode):
            raise OSError("permission denied")

        monkeypatch.setattr(os, "chmod", _boom)

        manager = SettingsManager(tmp_path / "session.dat")

        assert manager.get_show_token_counter() is False


class TestCorruptedStateBackupIsAlsoChmodded:
    """ADR-004 stage 4.4 (adversarial-review fix): _backup_corrupt_state_file
    preserves an unreadable session.dat "for forensic recovery" by renaming
    it aside - a rename preserves the source inode's mode bits exactly, so
    without an explicit chmod this backup (which can hold the same
    encrypted-or-plaintext secrets the live file had, and is never touched
    again by anything else in the codebase) inherited whatever looser
    permissions the original corrupt file happened to have."""

    def test_chmod_is_invoked_on_the_backup_file(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(os, "chmod", lambda path, mode: calls.append((path, mode)))

        state_file = tmp_path / "session.dat"
        state_file.write_text("{not valid json", encoding="utf-8")

        SettingsManager(state_file)

        backup_path = next(tmp_path.glob("session.dat.corrupted-*"))
        assert (backup_path, 0o600) in calls

    def test_posix_permission_bits_on_the_backup_are_actually_0600(self, tmp_path):
        if sys.platform == "win32":
            pytest.skip("chmod is a no-op on Windows - see class docstring")

        state_file = tmp_path / "session.dat"
        state_file.write_text("{not valid json", encoding="utf-8")
        os.chmod(state_file, 0o644)

        SettingsManager(state_file)

        backup_path = next(tmp_path.glob("session.dat.corrupted-*"))
        assert stat.S_IMODE(backup_path.stat().st_mode) == 0o600

    def test_a_chmod_failure_on_the_backup_does_not_crash_construction(self, tmp_path, monkeypatch):
        def _boom(path, mode):
            raise OSError("permission denied")

        monkeypatch.setattr(os, "chmod", _boom)
        state_file = tmp_path / "session.dat"
        state_file.write_text("{not valid json", encoding="utf-8")

        manager = SettingsManager(state_file)

        assert manager.get_show_token_counter() is False


class TestTmpFileIsChmoddedBeforeContentIsWritten:
    """ADR-004 stage 4.4 (adversarial-review fix): the original code
    wrote+fsync'd the full pending state to session.dat.tmp and only THEN
    chmod'd it. A crash (power loss, OOM-kill) in that window left an
    orphaned tmp file holding the pending secret values at loose,
    umask-default permissions - and nothing in the codebase ever revisits
    that exact filename except the NEXT successful save, so the exposure
    could persist across many launches. Chmod'ing the file immediately
    after it's opened (while still empty, before any secret bytes are
    written) closes that window."""

    def test_tmp_file_chmod_happens_while_the_file_is_still_empty(self, tmp_path, monkeypatch):
        # No platform skip needed (unlike the permission-bit tests
        # elsewhere in this file) - this test only checks CALL ORDERING via
        # a spy, which is platform-independent even though chmod's real
        # POSIX effect is not.
        state_file = tmp_path / "session.dat"
        observed_sizes_at_tmp_chmod_time = []
        real_chmod = os.chmod

        def spy_chmod(path, mode):
            if str(path).endswith(".tmp"):
                observed_sizes_at_tmp_chmod_time.append(os.path.getsize(path))
            real_chmod(path, mode)

        monkeypatch.setattr(os, "chmod", spy_chmod)

        SettingsManager(state_file)

        assert observed_sizes_at_tmp_chmod_time
        assert all(size == 0 for size in observed_sizes_at_tmp_chmod_time)
