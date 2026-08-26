"""PersistenceOps - the file/migration/persistence engine for SettingsManager.

A MIXIN, not a standalone class: every method operates on the composing
class's own state (self.state/self.state_file/self._state_needs_save/
self._secrets_encrypted_at_rest) - it is composed exactly once, by
graphlink_settings_store.py's `class SettingsManager(PersistenceOps, ...)`.
__init__ here is what sets up those shared instance attributes every other
mixin reads; the class-level constants each migration references
(NOTIFICATION_TYPES, CURRENT_SCHEMA_VERSION, SECRET_KEYS, ...) stay on the
composed SettingsManager class itself, not here, so no mixin needs to guess
which sibling mixin "owns" a given constant.

Method bodies are relocated VERBATIM from graphlink_settings_store.py; only
the class wrapper and imports are new.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import graphlink_secrets
from graphlink_migrations import run_dict_migrations
from graphlink_model_catalog import (
    AUTO_MODEL,
    INHERIT_MODEL,
    ModelAssignment,
    assignment_values,
    normalize_model_id,
)

logger = logging.getLogger(__name__)


class PersistenceOps:

    def __init__(self, state_file: Path | str | None = None):
        self.state_file = Path(state_file) if state_file is not None else Path.home() / '.graphlink' / 'session.dat'
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_needs_save = False
        self.state = self._load_state()
        if self._state_needs_save:
            self._save_state()
        # ADR-004 stage 4.4: this is only the INITIAL value - not re-probed
        # on every settings_payload read, since dpapi_available() does a
        # real WinAPI round-trip. It IS kept honest afterward: every real
        # secret save routes through _protect_and_track (see that method's
        # own docstring), which updates this flag from the actual outcome
        # of that specific protect() call - a fix for an adversarial-review
        # finding where a construction-time-only probe could silently
        # diverge from reality (DPAPI failing or recovering between
        # construction and a later save left the UI banner stuck on a
        # stale answer). This initial probe must still run BEFORE
        # _migrate_plaintext_secrets below so that call's own
        # protect()-driven migration and this flag agree on the same
        # DPAPI state from the very first observation.
        self._secrets_encrypted_at_rest = graphlink_secrets.dpapi_available()
        self._migrate_plaintext_secrets()
        # ADR-004 stage 4.4: self-heal permissions on every launch, not
        # just on the next write - see _save_state's own comment for why
        # POSIX 0600 matters here (session.dat holds the encrypted-or-
        # plaintext API keys/GitHub token). No-op on Windows: os.chmod
        # there only toggles the read-only attribute bit, which a
        # freshly-written settings file never has anyway - real access
        # control on Windows comes from the per-user profile + DPAPI's own
        # account binding, not POSIX permission bits.
        if self.state_file.exists():
            try:
                os.chmod(self.state_file, 0o600)
            except OSError:
                logger.warning(
                    "could not chmod %s to 0600 - continuing with existing permissions", self.state_file
                )

    def secrets_encrypted_at_rest(self) -> bool:
        """ADR-004 stage 4.4: the flag backend/settings.py's wire payload
        surfaces as secretsEncryptedAtRest, and the Settings UI renders as
        a persistent badge when False ("API keys are stored unencrypted on
        this system") - see graphlink_secrets.dpapi_available's own
        docstring for what the INITIAL value means, and _protect_and_track's
        docstring for how it's kept accurate across real secret saves after
        that."""
        return self._secrets_encrypted_at_rest

    def _protect_and_track(self, value: str) -> str:
        """graphlink_secrets.protect(), plus refreshing the cached
        secrets_encrypted_at_rest() flag from a REAL probe taken at the
        same moment - adversarial-review fix for stage 4.4: the flag was
        originally computed only once, from a synthetic probe at
        construction, and never revisited. DPAPI genuinely failing (or
        recovering) between construction and a later real secret save left
        the flag - and the Settings UI banner it drives - stuck on a stale,
        wrong answer for the rest of the process's life: a save could
        silently fall back to plaintext while the banner kept claiming
        everything was encrypted, or vice versa after a real recovery.

        Deliberately re-probes via dpapi_available() rather than inferring
        the outcome from `protected`'s shape (e.g. "does it start with
        dpapi:") - a first attempt at this did exactly that and had its own
        bug: protect() on an ALREADY-encrypted value that fails to
        re-verify falls back to returning the input UNCHANGED, which still
        carries the "dpapi:" prefix from before, so a shape check alone
        would keep reporting encrypted==True even while DPAPI was fully
        down for a re-saved existing secret. A fresh, value-independent
        probe has no such blind spot.

        Skipped for an empty value - saving a blank field is not a
        meaningful moment to re-probe, and doing so 2-3x per
        set_api_settings call (once per provider field) even when nothing
        was actually typed would be pure waste."""
        normalized = str(value or "")
        protected = graphlink_secrets.protect(normalized)
        if normalized:
            self._secrets_encrypted_at_rest = graphlink_secrets.dpapi_available()
        return protected

    def _migrate_plaintext_secrets(self):
        """Encrypt any legacy plaintext secret still on disk from before #14 was fixed.

        Runs once per launch: if DPAPI is available and a secret field holds a
        plaintext value, re-protect it and persist immediately so the plaintext leaves
        disk on the first launch after upgrading, not whenever the user next happens
        to touch a setting. Where DPAPI is unavailable, protect() returns the value
        unchanged, so nothing is rewritten and nothing regresses.

        Adversarial-review fix: only ever acts on a value that has NO "dpapi:"
        prefix at all (a cheap, unambiguous check - graphlink_secrets.is_protected).
        A value that already carries the prefix is left untouched here,
        regardless of whether it happens to be currently decryptable.

        This closes a real bug: the previous version called protect() on
        every stored value unconditionally. protect()'s own idempotency
        check (_is_encrypted_blob) tries to DECRYPT the value to decide
        "already encrypted, leave alone" - and for a genuine DPAPI blob
        that simply isn't decryptable right now (session.dat copied to a
        different Windows account/machine, or a corrupted blob), decryption
        fails, so protect() concluded it must be plaintext and
        RE-ENCRYPTED (double-wrapped) it under the current account's key.
        From then on the getter would successfully decrypt this new
        wrapper and return the literal base64 text of the original
        undecryptable blob as if it were the real secret - silent garbage,
        not the documented, tested "" that unprotect() promises for an
        undecryptable blob (test_undecryptable_blob_returns_empty_not_garbage).
        A user migrating a profile to a new PC would see no warning
        (secrets_encrypted_at_rest() stays True - DPAPI genuinely works on
        the new account) but their provider would silently fail to
        authenticate with garbage instead of cleanly prompting for a new
        key.

        Accepted tradeoff: a secret that is somehow stored as literal
        PLAINTEXT that itself happens to start with "dpapi:" (e.g. typed as
        a proxy master key while DPAPI was unavailable) will no longer be
        auto-migrated to real encryption here - protect()'s own
        prefix-collision handling (see its docstring) only ever mattered
        for a value passed to protect() directly, e.g. from a fresh save;
        there is no way to distinguish that case from a genuine
        undecryptable blob without attempting decryption, which is exactly
        the ambiguous check that caused the bug this closes. Silently
        risking a corrupted credential turning into an accepted garbage
        key is worse than a contrived, unmigrated edge case whose getter
        already cleanly returns "" today whenever its literal text isn't
        also valid base64 (the overwhelmingly common case for real typed
        text) - so this tradeoff was deliberately made in the safer
        direction."""
        migrated = False
        for key in self.SECRET_KEYS:
            current_value = str(self.state.get(key, "") or "")
            if not current_value or graphlink_secrets.is_protected(current_value):
                continue
            protected_value = self._protect_and_track(current_value)
            if protected_value != current_value:
                self.state[key] = protected_value
                migrated = True
        if migrated:
            self._save_state()

    # ADR-009 stage 9.1: keyed by the version each function PRODUCES
    # (migration "1" takes a state dict from 0 -> 1), matching
    # graphlink_migrations' own ordering convention and
    # backend/chat_library.py's sibling _MIGRATIONS table - see
    # run_dict_migrations' own docstring for the calling contract each
    # function below must honor (receive the in-progress dict, return the
    # migrated dict, never manage a "changed" side channel - _load_state
    # derives that from a single whole-state comparison instead, see its
    # own comment). Built fresh per call (not a class-level dict) because
    # these are bound instance methods - _migration_002_provider_scoped_
    # cloud_profiles reaches self._migrate_model_settings, and
    # _migration_001_baseline_fields reaches self.NOTIFICATION_TYPES.
    def _dict_migrations(self):
        return {
            1: self._migration_001_baseline_fields,
            2: self._migration_002_provider_scoped_cloud_profiles,
            3: self._migration_003_cloud_model_catalogs,
            4: self._migration_004_graded_reasoning_levels,
        }

    def _migration_001_baseline_fields(self, state: dict) -> dict:
        """Migration "1" (0 -> 1): every field that already existed in
        session.dat before schema_version existed at all (see commit
        6a7e326, "schema_version fields added to session.dat and chat
        payloads" - CURRENT_SCHEMA_VERSION was introduced there as 1, over a
        file shape every one of these fields was already part of). None of
        these were ever gated on a version number, only on the field's own
        presence - ported verbatim from the old scattered
        `if 'x' not in state` chain that used to live directly in
        _load_state."""
        if 'show_token_counter' not in state:
            state['show_token_counter'] = False
        if 'ollama_chat_model' not in state:
            state['ollama_chat_model'] = ''
        if 'ollama_title_model' not in state:
            state['ollama_title_model'] = ''
        if 'ollama_chart_model' not in state:
            state['ollama_chart_model'] = ''
        if 'ollama_web_validate_model' not in state:
            state['ollama_web_validate_model'] = ''
        if 'ollama_web_summarize_model' not in state:
            state['ollama_web_summarize_model'] = ''
        if 'ollama_scanned_models' not in state:
            state['ollama_scanned_models'] = []
        if 'ollama_model_scan_mode' not in state:
            state['ollama_model_scan_mode'] = ''
        if 'ollama_model_scan_path' not in state:
            state['ollama_model_scan_path'] = ''
        if 'ollama_model_scan_locations' not in state:
            state['ollama_model_scan_locations'] = []
        if 'llama_cpp_chat_model_path' not in state:
            state['llama_cpp_chat_model_path'] = ''
        if 'llama_cpp_title_model_path' not in state:
            state['llama_cpp_title_model_path'] = ''
        if 'llama_cpp_chat_format' not in state:
            state['llama_cpp_chat_format'] = ''
        if 'llama_cpp_n_ctx' not in state:
            state['llama_cpp_n_ctx'] = 4096
        if 'llama_cpp_n_gpu_layers' not in state:
            state['llama_cpp_n_gpu_layers'] = 0
        if 'llama_cpp_n_threads' not in state:
            state['llama_cpp_n_threads'] = 0
        if 'llama_cpp_scanned_models' not in state:
            state['llama_cpp_scanned_models'] = []
        if 'llama_cpp_model_scan_mode' not in state:
            state['llama_cpp_model_scan_mode'] = ''
        if 'llama_cpp_model_scan_path' not in state:
            state['llama_cpp_model_scan_path'] = ''
        if 'llama_cpp_model_scan_locations' not in state:
            state['llama_cpp_model_scan_locations'] = []
        if 'current_mode' not in state:
            state['current_mode'] = 'Ollama (Local)'
        if 'api_provider' not in state:
            state['api_provider'] = 'OpenAI-Compatible'
        if 'api_base_url' not in state:
            state['api_base_url'] = 'https://api.openai.com/v1'
        if 'openai_api_key' not in state:
            state['openai_api_key'] = ''
        if 'anthropic_api_key' not in state:
            state['anthropic_api_key'] = ''
        if 'gemini_api_key' not in state:
            state['gemini_api_key'] = ''
        if 'github_access_token' not in state:
            state['github_access_token'] = ''
        if 'api_models' not in state:
            state['api_models'] = {}
        if 'enable_system_prompt' not in state:
            state['enable_system_prompt'] = True
        if 'update_notifications_enabled' not in state:
            state['update_notifications_enabled'] = False
        if 'notification_preferences' not in state or not isinstance(state.get('notification_preferences'), dict):
            state['notification_preferences'] = {}
        for notification_type in self.NOTIFICATION_TYPES:
            if notification_type not in state['notification_preferences']:
                state['notification_preferences'][notification_type] = True
        if 'update_status_message' not in state:
            state['update_status_message'] = 'Automatic update checks are off.'
        if 'update_status_level' not in state:
            state['update_status_level'] = 'info'
        if 'update_last_checked_at' not in state:
            state['update_last_checked_at'] = ''
        if 'update_latest_version' not in state:
            state['update_latest_version'] = ''
        if 'update_available' not in state:
            state['update_available'] = False
        return state

    def _migration_002_provider_scoped_cloud_profiles(self, state: dict) -> dict:
        """Migration "2" (1 -> 2, commit a1dbaeda): provider-scoped cloud
        model profiles (api_models_by_provider) and explicit local model
        assignment modes (ollama_model_assignments, via the pre-existing
        _migrate_model_settings helper) - see that commit's own
        CURRENT_SCHEMA_VERSION bump comment ("provider-scoped cloud profiles
        and explicit local model assignment modes"). Depends on
        ollama_chat_model/ollama_title_model/ollama_chart_model/
        ollama_web_validate_model/ollama_web_summarize_model/api_provider/
        api_models all already being present, which migration "1" above
        guarantees by running first."""
        if 'api_models_by_provider' not in state or not isinstance(state.get('api_models_by_provider'), dict):
            # SECURITY-FIX: dict(state.get('api_models', {}) or {}) raised an
            # uncaught ValueError when a hand-corrupted/hostile session.dat
            # carried a non-empty list for api_models ("dictionary update
            # sequence element #0 has length N"), escaping _load_state's own
            # corrupt-file handling and crashing the app at boot. A wrong-
            # typed api_models is corruption, not data - fall back to the
            # same empty default a missing field gets, rather than raising.
            raw_api_models = state.get('api_models', {})
            seed_models = dict(raw_api_models) if isinstance(raw_api_models, dict) else {}
            state['api_models_by_provider'] = {
                str(state.get('api_provider', 'OpenAI-Compatible')): seed_models
            }
        self._migrate_model_settings(state)
        return state

    def _migration_003_cloud_model_catalogs(self, state: dict) -> dict:
        """Migration "3" (2 -> 3, commit 7a1f19e): persisted refreshed cloud
        model catalogs (api_model_catalog_by_provider), so the composer can
        offer a useful selector without a network request on every render -
        see that commit's own CURRENT_SCHEMA_VERSION bump comment."""
        if 'api_model_catalog_by_provider' not in state or not isinstance(state.get('api_model_catalog_by_provider'), dict):
            state['api_model_catalog_by_provider'] = {}
        return state

    def _migration_004_graded_reasoning_levels(self, state: dict) -> dict:
        """Migration "4" (3 -> 4, commit ed467c9 / R8a): the 2-value
        Ollama/Llama.cpp reasoning "mode" (Thinking/Quick) becomes a graded
        4-value "level" (off/low/medium/high) shared by all 5 providers -
        migrate any already-persisted choice faithfully rather than
        resetting it ("Quick" meant no reasoning at all -> off, "Thinking"
        meant full reasoning -> high, this field's own default), and add the
        3 new cloud reasoning-level fields that had no equivalent before,
        defaulting to off (extended thinking on a paid API is an opt-in
        cost/latency tradeoff, never a silent default, unlike the local
        providers whose compute is free to the user) - see that commit's own
        CURRENT_SCHEMA_VERSION bump comment.

        mcp_servers (ADR-007 stage 7.5) and plugin_grants (ADR-014 stage
        14.4) are both grouped in here too even though neither was added
        alongside its own CURRENT_SCHEMA_VERSION bump - there is no version
        number to place either at. Since every migration in this chain
        always runs on every load regardless of the file's own declared
        version (see _load_state's own comment on why), attaching them to
        the chain's current terminal function is the deliberate,
        least-surprising home for them, not a guess."""
        if 'ollama_reasoning_level' not in state:
            # R8a: reasoning went from a 2-value Ollama/Llama.cpp bool
            # "mode" to a graded 4-value level shared by every provider -
            # migrate any already-persisted choice faithfully rather than
            # silently resetting it: "Quick" meant no reasoning at all
            # (-> off), "Thinking" meant full reasoning (-> high, this
            # field's own default).
            state['ollama_reasoning_level'] = 'off' if state.get('ollama_reasoning_mode') == 'Quick' else 'high'
        state.pop('ollama_reasoning_mode', None)
        if 'llama_cpp_reasoning_level' not in state:
            # Same migration story as ollama_reasoning_level above.
            state['llama_cpp_reasoning_level'] = 'off' if state.get('llama_cpp_reasoning_mode') == 'Quick' else 'high'
        state.pop('llama_cpp_reasoning_mode', None)
        if 'anthropic_reasoning_level' not in state:
            # New cloud-provider fields (R8a) - "off" by default, matching
            # api_provider.py's own conservative default: extended thinking
            # on a paid API is an opt-in cost/latency tradeoff, never a
            # silent default.
            state['anthropic_reasoning_level'] = 'off'
        if 'gemini_reasoning_level' not in state:
            state['gemini_reasoning_level'] = 'off'
        if 'openai_reasoning_level' not in state:
            state['openai_reasoning_level'] = 'off'
        if 'mcp_servers' not in state or not isinstance(state.get('mcp_servers'), list):
            # ADR-007 stage 7.5: absent in every pre-7.5 save -> no
            # configured MCP servers, matching the initial-state default in
            # _create_initial_state - a settings surface with nothing
            # configured yet is the correct, safe starting point, not an
            # error.
            state['mcp_servers'] = []
        if 'plugin_grants' not in state or not isinstance(state.get('plugin_grants'), dict):
            # ADR-014 stage 14.4: install-time consent grants for discovered
            # third-party plugins - same "no version number to place it at,
            # attach it to the chain's current terminal function" posture as
            # mcp_servers directly above. Absent -> {}, matching the
            # initial-state default in _create_initial_state: a plugin_id
            # with no entry here is NOT granted (deny-by-default).
            state['plugin_grants'] = {}
        return state

    def _load_state(self):
        if not self.state_file.exists():
            return self._create_initial_state()
        try:
            # encoding is explicit: this file is written as UTF-8 (_save_state
            # opens for write with the same default text mode on the machine
            # that produced it), but reading it back through the LOCALE codec
            # made byte-level corruption fatal in a way the corrupt-file
            # rescue below was written to prevent. Disk corruption that lands
            # a byte the locale codec rejects (0x81/0x8D/0x9D under cp1252,
            # far more under a CJK codepage) raises UnicodeDecodeError - a
            # ValueError, caught by NEITHER handler here - and that escapes
            # SettingsManager.__init__, which is constructed unguarded at boot
            # (backend/app.py's create_app, and earlier still in
            # graphlink_desktop.main). The app then fails to launch on every
            # single start until the user finds and deletes the file by hand.
            # UnicodeDecodeError is caught below for exactly that reason: a
            # corrupt settings file must be backed up and replaced, never a
            # permanent boot failure.
            with open(self.state_file, 'r', encoding='utf-8') as f:
                raw_state = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, IOError) as e:
            self._backup_corrupt_state_file(e)
            return self._create_initial_state()

        # SECURITY-FIX: json.load happily returns a non-dict top level for a
        # syntactically valid file whose root is `[]`, `"x"`, `42`, or
        # `null`. The migrations below immediately do `key in state` /
        # `state[key] = ...` and raise TypeError on any of those, which the
        # except above (JSONDecodeError/UnicodeDecodeError/IOError only) does
        # NOT catch - so a hostile or hand-corrupted session.dat crashed the
        # whole app at construction (backend/app.py create_app, and earlier
        # graphlink_desktop.main) on every launch, the exact permanent-boot-
        # failure this region's own comments say must never happen. A wrong-
        # typed root is corruption just like unparseable bytes are, so it
        # takes the same backup-and-replace path rather than a raise.
        if not isinstance(raw_state, dict):
            self._backup_corrupt_state_file(
                TypeError(f"session.dat root is {type(raw_state).__name__}, expected object")
            )
            return self._create_initial_state()

        # ADR-009 stage 9.1: every backfill above now runs through
        # graphlink_migrations.run_dict_migrations instead of one long
        # inline `if 'field' not in state: ...` chain. Deliberately always
        # called with current_version=0 here - never
        # `raw_state.get('schema_version', 0)` - so migrations 1 through
        # CURRENT_SCHEMA_VERSION run on EVERY load, exactly matching what
        # the old inline chain did: those if-checks were never actually
        # gated on the file's own declared schema_version at all (that is
        # the "scattered, no explicit boundary" shape this refactor was
        # asked to fix structurally, not a version-gated migration system to
        # begin with) - they ran unconditionally, keyed only on whether each
        # individual field was already present. Switching to a genuinely
        # version-gated read (skipping migration N whenever the stored
        # schema_version is already >= N) would be a real behavior change: a
        # file that already claims the current version but is missing a
        # field an earlier migration would have added (hand-edited,
        # corrupted, or written by a future build this one doesn't fully
        # understand) would stop being self-healed on load. Each migration
        # function is idempotent (the same `if key not in state` shape as
        # before), so re-running all of them against an already-fully-
        # populated state is a correct, cheap no-op - not wasted risk.
        migrated_state, _ = run_dict_migrations(
            raw_state, 0, self.CURRENT_SCHEMA_VERSION, self._dict_migrations()
        )

        # The persisted version stamp itself is bumped separately from the
        # migrations dict above, since its "never move it backward" rule
        # doesn't fit run_dict_migrations' landed-on-target contract: a file
        # that already declares a NEWER version than this build knows about
        # (opened by an older build after a downgrade) must keep that
        # number, not have it overwritten with this build's lower
        # CURRENT_SCHEMA_VERSION.
        if 'schema_version' not in migrated_state:
            migrated_state['schema_version'] = self.CURRENT_SCHEMA_VERSION
        else:
            _stored_schema_version = migrated_state.get('schema_version')
            # REVIEW-FIX: none of the migrations above touch 'schema_version'
            # itself, so a syntactically-valid-JSON but non-numeric value
            # here (null, a string, a list, a dict - from disk-level
            # corruption or the hand-edited session.dat this exact region's
            # comments above already anticipate) passes straight through
            # untouched. The `<` comparison this used to run directly raised
            # an uncaught TypeError for any such value, escaping _load_state
            # and taking down SettingsManager.__init__ - and the whole app
            # at boot, on every single launch - with no self-heal path (the
            # JSONDecodeError/UnicodeDecodeError/IOError handler above only
            # catches parse-level failures, not this). bool is excluded even
            # though it is technically an int subclass: a stray True/False
            # here is exactly the kind of "not really a version number"
            # value this guard exists to catch. Treating a non-numeric value
            # the same as a missing one - landing on CURRENT_SCHEMA_VERSION -
            # routes it through the same backfill posture as a file that
            # never had the field at all, instead of crashing.
            if not isinstance(_stored_schema_version, (int, float)) or isinstance(_stored_schema_version, bool):
                migrated_state['schema_version'] = self.CURRENT_SCHEMA_VERSION
            elif _stored_schema_version < self.CURRENT_SCHEMA_VERSION:
                migrated_state['schema_version'] = self.CURRENT_SCHEMA_VERSION

        # Save immediately only if this load actually changed something -
        # the refactored equivalent of the old per-field state_changed flag
        # (persist a migrated file right away rather than leaving it only in
        # memory until the next explicit set_*() call), expressed as one
        # whole-state comparison instead of that flag's own inconsistent
        # per-field coverage (several fields - e.g. a missing
        # ollama_title_model alone - never flipped it at all in the old
        # code). run_dict_migrations guarantees raw_state itself is never
        # mutated in place, so this compares the untouched on-disk shape
        # against the fully migrated+stamped result.
        if migrated_state != raw_state:
            self._state_needs_save = True

        return migrated_state

    def _backup_corrupt_state_file(self, error):
        # Preserve the unreadable file for forensic recovery instead of silently
        # overwriting it with defaults - previously a corrupt session.dat (which,
        # pre-atomic-write, could happen from a crash mid-save) was destroyed with no
        # trace and no warning the moment it failed to parse.
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = self.state_file.with_name(f"{self.state_file.name}.corrupted-{timestamp}")
            self.state_file.replace(backup_path)
            # ADR-004 stage 4.4 (adversarial-review fix): Path.replace is a
            # rename, which preserves the source inode's mode bits exactly -
            # this backup can hold the same encrypted-or-plaintext secrets
            # the live file had, and is never touched again by any other
            # code path (kept indefinitely "for forensic recovery"), so it
            # needs its own explicit chmod rather than inheriting whatever
            # permissions the original file happened to have.
            try:
                os.chmod(backup_path, 0o600)
            except OSError:
                logger.warning("could not chmod %s to 0600 - continuing with existing permissions", backup_path)
            logger.warning(
                "%s could not be read (%s). Backed it up to %s and reset settings to defaults.",
                self.state_file, error, backup_path,
            )
        except OSError as backup_error:
            logger.warning(
                "%s could not be read (%s) and could not be backed up (%s). Resetting settings to defaults.",
                self.state_file, error, backup_error,
            )

    def _create_initial_state(self):
        state = {
            "schema_version": self.CURRENT_SCHEMA_VERSION,
            "show_token_counter": False,
            "ollama_chat_model": "",
            "ollama_title_model": "",
            "ollama_chart_model": "",
            "ollama_web_validate_model": "",
            "ollama_web_summarize_model": "",
            "ollama_model_assignments": {
                "task_title": {"mode": INHERIT_MODEL, "model_id": ""},
                "task_chat": {"mode": AUTO_MODEL, "model_id": ""},
                "task_chart": {"mode": INHERIT_MODEL, "model_id": ""},
                "task_web_validate": {"mode": INHERIT_MODEL, "model_id": ""},
                "task_web_summarize": {"mode": INHERIT_MODEL, "model_id": ""},
            },
            "ollama_reasoning_level": "high",
            "ollama_scanned_models": [],
            "ollama_model_scan_mode": "",
            "ollama_model_scan_path": "",
            "ollama_model_scan_locations": [],
            "llama_cpp_chat_model_path": "",
            "llama_cpp_title_model_path": "",
            "llama_cpp_reasoning_level": "high",
            "llama_cpp_chat_format": "",
            "llama_cpp_n_ctx": 4096,
            "llama_cpp_n_gpu_layers": 0,
            "llama_cpp_n_threads": 0,
            "llama_cpp_scanned_models": [],
            "llama_cpp_model_scan_mode": "",
            "llama_cpp_model_scan_path": "",
            "llama_cpp_model_scan_locations": [],
            "current_mode": "Ollama (Local)",
            "api_provider": "OpenAI-Compatible",
            "api_base_url": "https://api.openai.com/v1",
            "openai_api_key": "",
            "anthropic_api_key": "",
            "gemini_api_key": "",
            "github_access_token": "",
            # R8a: off by default - extended thinking on a paid API is an
            # opt-in cost/latency tradeoff, never a silent default (unlike
            # the local providers above, whose compute is free to the user).
            "anthropic_reasoning_level": "off",
            "gemini_reasoning_level": "off",
            "openai_reasoning_level": "off",
            "api_models": {},
            "api_models_by_provider": {},
            "api_model_catalog_by_provider": {},
            "enable_system_prompt": True,
            "update_notifications_enabled": False,
            "notification_preferences": {notification_type: True for notification_type in self.NOTIFICATION_TYPES},
            "update_status_message": "Automatic update checks are off.",
            "update_status_level": "info",
            "update_last_checked_at": "",
            "update_latest_version": "",
            "update_available": False,
            # ADR-007 stage 7.5: MCP client configuration - see
            # get_mcp_servers/set_mcp_servers' own docstrings.
            "mcp_servers": [],
            # ADR-014 stage 14.4: install-time consent grants for discovered
            # third-party plugins - see get_plugin_grants/set_plugin_grant's
            # own docstrings.
            "plugin_grants": {},
        }
        self._save_state(state)
        return state

    def _migrate_model_settings(self, state: dict) -> bool:
        """Migrate legacy model strings without activating product defaults."""
        changed = False
        raw_assignments = state.get("ollama_model_assignments")
        if not isinstance(raw_assignments, dict):
            raw_assignments = {}
            for task in self.OLLAMA_MODEL_TASKS:
                legacy_key = {
                    "task_title": "ollama_title_model",
                    "task_chat": "ollama_chat_model",
                    "task_chart": "ollama_chart_model",
                    "task_web_validate": "ollama_web_validate_model",
                    "task_web_summarize": "ollama_web_summarize_model",
                }[task]
                legacy_value = normalize_model_id(state.get(legacy_key, ""))
                if legacy_value.lower() in self.LEGACY_PRODUCT_MODEL_IDS:
                    mode = AUTO_MODEL if task == "task_chat" else INHERIT_MODEL
                    raw_assignments[task] = ModelAssignment(mode).to_dict()
                elif legacy_value:
                    raw_assignments[task] = ModelAssignment("explicit", legacy_value).to_dict()
                else:
                    mode = AUTO_MODEL if task == "task_chat" else INHERIT_MODEL
                    raw_assignments[task] = ModelAssignment(mode).to_dict()
            changed = True

        normalized = assignment_values(raw_assignments)
        for task, value in list(normalized.items()):
            assignment = ModelAssignment.from_value(value)
            if assignment.mode == "explicit" and assignment.model_id.lower() in self.LEGACY_PRODUCT_MODEL_IDS:
                normalized[task] = ModelAssignment(
                    AUTO_MODEL if task == "task_chat" else INHERIT_MODEL
                ).to_dict()
        if state.get("ollama_model_assignments") != normalized:
            state["ollama_model_assignments"] = normalized
            changed = True

        # Keep legacy fields synchronized for older builds that may inspect the
        # state file, but never write a product-authored default into them.
        for task, key in {
            "task_title": "ollama_title_model",
            "task_chat": "ollama_chat_model",
            "task_chart": "ollama_chart_model",
            "task_web_validate": "ollama_web_validate_model",
            "task_web_summarize": "ollama_web_summarize_model",
        }.items():
            assignment = ModelAssignment.from_value(normalized.get(task))
            legacy_value = assignment.model_id if assignment.mode == "explicit" else ""
            if state.get(key, "") != legacy_value:
                state[key] = legacy_value
                changed = True
        return changed

    def _save_state(self, state_data=None):
        # Write to a temp file and atomically rename it into place (os.replace is
        # atomic on both Windows and POSIX when source/dest are on the same volume,
        # guaranteed here since the temp file lives next to state_file). Previously
        # this wrote directly to state_file - a crash or power loss mid-write left a
        # truncated/corrupt file, which _load_state's JSONDecodeError handler then
        # silently replaced with defaults, destroying every saved API key and
        # preference with no warning. Now a crash can only ever leave the *temp* file
        # incomplete; state_file itself is always either the old complete version or
        # the new complete version, never something in between.
        data_to_save = state_data if state_data else self.state
        tmp_path = self.state_file.with_name(self.state_file.name + ".tmp")
        try:
            # encoding is explicit to match _load_state's own explicit utf-8
            # read. json.dump defaults to ensure_ascii=True, so what actually
            # lands here is pure ASCII either way and every previously-written
            # file stays readable - this just removes the latent dependency on
            # whatever locale codec the host happens to default to.
            with open(tmp_path, 'w', encoding='utf-8') as f:
                # ADR-004 stage 4.4 (adversarial-review fix): chmod the temp
                # file immediately after opening it, BEFORE writing any
                # content - chmod'ing only after fsync left a window where a
                # crash (power loss, OOM-kill) between the write and the
                # chmod orphaned a fully-written tmp file, holding the
                # pending secret values, at umask-default (loose)
                # permissions. Nothing on the next launch ever revisits that
                # exact filename except the NEXT successful save, so the
                # exposure could persist across many launches. Chmod'ing the
                # file while it's still empty (right after open() creates
                # it, before any secret bytes land) closes that window
                # entirely. No-op on Windows (see __init__'s own comment on
                # why POSIX permission bits don't apply there).
                try:
                    os.chmod(tmp_path, 0o600)
                except OSError:
                    logger.warning("could not chmod %s to 0600 before writing - continuing", tmp_path)
                json.dump(data_to_save, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.state_file)
        except IOError as e:
            logger.error("Could not save session state to %s. Reason: %s", self.state_file, e)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def get_schema_version(self):
        return self.state.get("schema_version", self.CURRENT_SCHEMA_VERSION)
