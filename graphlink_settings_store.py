import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import graphlink_secrets
from graphlink_model_catalog import (
    AUTO_MODEL,
    INHERIT_MODEL,
    ModelAssignment,
    assignment_values,
    normalize_model_id,
)

logger = logging.getLogger(__name__)


def _is_llama_cpp_gguf_path(path_value) -> bool:
    normalized = str(path_value or "").strip()
    return bool(normalized) and normalized.lower().endswith(".gguf")


class SettingsManager:
    NOTIFICATION_TYPES = ("info", "success", "warning", "error")
    # R8a: the graded reasoning-effort vocabulary shared by all 5 providers'
    # reasoning-level fields below - see api_provider.py's own REASONING_LEVELS
    # docstring for the full per-provider mapping story this feeds.
    REASONING_LEVELS = ("off", "low", "medium", "high")
    # Bumped whenever session.dat's shape changes in a way future code needs to branch
    # on. Version 2 introduces provider-scoped cloud profiles and explicit local
    # model assignment modes. Version 3 persists refreshed cloud model catalogs so
    # the composer can offer a useful selector without making a network request on
    # every render. Version 4 replaces the 2-value Ollama/Llama.cpp reasoning
    # "mode" (Thinking/Quick) with a graded 4-value "level" (off/low/medium/
    # high) shared by all 5 providers, adding real reasoning-effort fields for
    # Anthropic/Gemini/OpenAI-compatible where none existed before.
    CURRENT_SCHEMA_VERSION = 4
    LEGACY_PRODUCT_MODEL_IDS = {"qwen3:8b", "deepseek-coder:6.7b"}
    OLLAMA_MODEL_TASKS = (
        "task_title",
        "task_chat",
        "task_chart",
        "task_web_validate",
        "task_web_summarize",
    )

    """
    Manages all persistent application state and user settings.

    This class reads from and writes to a local state file (`session.dat`) to
    persist data across application launches.
    """
    # Settings fields that hold secrets - encrypted at rest via graphlink_secrets
    # (Windows DPAPI, "dpapi:"-prefixed values; see that module for the tradeoffs).
    SECRET_KEYS = ("openai_api_key", "anthropic_api_key", "gemini_api_key", "github_access_token")

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

    def _load_state(self):
        if not self.state_file.exists():
            return self._create_initial_state()
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                if 'show_token_counter' not in state:
                    state['show_token_counter'] = False
                state_changed = False
                if 'ollama_chat_model' not in state:
                    state['ollama_chat_model'] = ''
                    state_changed = True
                if 'ollama_title_model' not in state:
                    state['ollama_title_model'] = ''
                if 'ollama_chart_model' not in state:
                    state['ollama_chart_model'] = ''
                if 'ollama_web_validate_model' not in state:
                    state['ollama_web_validate_model'] = ''
                if 'ollama_web_summarize_model' not in state:
                    state['ollama_web_summarize_model'] = ''
                if 'ollama_reasoning_level' not in state:
                    # R8a: reasoning went from a 2-value Ollama/Llama.cpp
                    # bool "mode" to a graded 4-value level shared by every
                    # provider - migrate any already-persisted choice
                    # faithfully rather than silently resetting it: "Quick"
                    # meant no reasoning at all (-> off), "Thinking" meant
                    # full reasoning (-> high, this field's own default).
                    state['ollama_reasoning_level'] = 'off' if state.get('ollama_reasoning_mode') == 'Quick' else 'high'
                state.pop('ollama_reasoning_mode', None)
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
                if 'llama_cpp_reasoning_level' not in state:
                    # Same migration story as ollama_reasoning_level above.
                    state['llama_cpp_reasoning_level'] = 'off' if state.get('llama_cpp_reasoning_mode') == 'Quick' else 'high'
                state.pop('llama_cpp_reasoning_mode', None)
                if 'anthropic_reasoning_level' not in state:
                    # New cloud-provider fields (R8a) - "off" by default,
                    # matching api_provider.py's own conservative default:
                    # extended thinking on a paid API is an opt-in cost/
                    # latency tradeoff, never a silent default.
                    state['anthropic_reasoning_level'] = 'off'
                if 'gemini_reasoning_level' not in state:
                    state['gemini_reasoning_level'] = 'off'
                if 'openai_reasoning_level' not in state:
                    state['openai_reasoning_level'] = 'off'
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
                    state_changed = True
                if 'api_models_by_provider' not in state or not isinstance(state.get('api_models_by_provider'), dict):
                    state['api_models_by_provider'] = {
                        str(state.get('api_provider', 'OpenAI-Compatible')): dict(state.get('api_models', {}) or {})
                    }
                    state_changed = True
                if 'api_model_catalog_by_provider' not in state or not isinstance(state.get('api_model_catalog_by_provider'), dict):
                    state['api_model_catalog_by_provider'] = {}
                    state_changed = True
                state_changed = self._migrate_model_settings(state) or state_changed
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
                if 'mcp_servers' not in state or not isinstance(state.get('mcp_servers'), list):
                    # ADR-007 stage 7.5: absent in every pre-7.5 save -> no
                    # configured MCP servers, matching the initial-state
                    # default below - a settings surface with nothing
                    # configured yet is the correct, safe starting point,
                    # not an error.
                    state['mcp_servers'] = []
                    state_changed = True
                if 'schema_version' not in state:
                    state['schema_version'] = self.CURRENT_SCHEMA_VERSION
                    state_changed = True
                elif state.get('schema_version', 0) < self.CURRENT_SCHEMA_VERSION:
                    state['schema_version'] = self.CURRENT_SCHEMA_VERSION
                    state_changed = True
                if state_changed:
                    self._state_needs_save = True
                return state
        except (json.JSONDecodeError, IOError) as e:
            self._backup_corrupt_state_file(e)
            return self._create_initial_state()

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
            print(
                f"Warning: {self.state_file} could not be read ({error}). "
                f"Backed it up to {backup_path} and reset settings to defaults."
            )
        except OSError as backup_error:
            print(
                f"Warning: {self.state_file} could not be read ({error}) and could not "
                f"be backed up ({backup_error}). Resetting settings to defaults."
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
            with open(tmp_path, 'w') as f:
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
            print(f"Error: Could not save session state to {self.state_file}. Reason: {e}")
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def get_schema_version(self):
        return self.state.get("schema_version", self.CURRENT_SCHEMA_VERSION)

    def get_show_token_counter(self):
        # R8a: off by default - the overlay is opt-in now, not opt-out.
        return self.state.get("show_token_counter", False)

    def set_show_token_counter(self, show: bool):
        self.state['show_token_counter'] = show
        self._save_state()

    def get_enable_system_prompt(self):
        return self.state.get("enable_system_prompt", True)

    def set_enable_system_prompt(self, enabled: bool):
        self.state["enable_system_prompt"] = bool(enabled)
        self._save_state()

    def get_notification_preferences(self):
        saved_preferences = self.state.get("notification_preferences", {}) or {}
        return {
            notification_type: bool(saved_preferences.get(notification_type, True))
            for notification_type in self.NOTIFICATION_TYPES
        }

    def get_notification_type_enabled(self, notification_type: str):
        normalized_type = str(notification_type or "info").strip().lower()
        return self.get_notification_preferences().get(normalized_type, True)

    def set_notification_preferences(self, preferences: dict):
        current_preferences = self.get_notification_preferences()
        for notification_type in self.NOTIFICATION_TYPES:
            if notification_type in preferences:
                current_preferences[notification_type] = bool(preferences[notification_type])
        self.state["notification_preferences"] = current_preferences
        self._save_state()

    def get_update_notifications_enabled(self):
        return self.state.get("update_notifications_enabled", False)

    def set_update_notifications_enabled(self, enabled: bool):
        self.state["update_notifications_enabled"] = bool(enabled)
        if enabled and self.state.get("update_status_message") == "Automatic update checks are off.":
            self.state["update_status_message"] = "Automatic update checks are enabled."
        elif not enabled:
            self.state["update_status_message"] = "Automatic update checks are off."
            self.state["update_status_level"] = "info"
        self._save_state()

    def get_update_status_message(self):
        return self.state.get("update_status_message", "Automatic update checks are off.")

    def get_update_status_level(self):
        return self.state.get("update_status_level", "info")

    def get_update_last_checked_at(self):
        return self.state.get("update_last_checked_at", "")

    def get_update_latest_version(self):
        return self.state.get("update_latest_version", "")

    def get_update_available(self):
        return self.state.get("update_available", False)

    def record_update_check_result(self, result: dict):
        result = result or {}
        self.state["update_status_message"] = str(result.get("message", "Update check finished.")).strip()
        self.state["update_status_level"] = str(result.get("level", "info")).strip() or "info"
        self.state["update_last_checked_at"] = str(
            result.get("checked_at") or datetime.now(timezone.utc).isoformat()
        )
        self.state["update_latest_version"] = str(result.get("remote_version", "")).strip()
        self.state["update_available"] = bool(result.get("update_available", False))
        self._save_state()

    def get_ollama_model_assignments(self):
        assignments = self.state.get("ollama_model_assignments", {})
        if not isinstance(assignments, dict):
            return {}
        return assignment_values(assignments)

    def set_ollama_model_assignments(self, assignments: dict):
        normalized = assignment_values(assignments)
        self.state["ollama_model_assignments"] = normalized
        for task, key in {
            "task_title": "ollama_title_model",
            "task_chat": "ollama_chat_model",
            "task_chart": "ollama_chart_model",
            "task_web_validate": "ollama_web_validate_model",
            "task_web_summarize": "ollama_web_summarize_model",
        }.items():
            assignment = ModelAssignment.from_value(normalized.get(task))
            self.state[key] = assignment.model_id if assignment.mode == "explicit" else ""
        self._save_state()

    def _get_ollama_model(self, task: str) -> str:
        assignment = ModelAssignment.from_value(
            self.get_ollama_model_assignments().get(task, {})
        )
        return assignment.model_id if assignment.mode == "explicit" else ""

    def _set_ollama_model(self, task: str, legacy_key: str, model_name: str):
        model_id = normalize_model_id(model_name)
        assignments = self.get_ollama_model_assignments()
        if model_id and model_id.lower() not in self.LEGACY_PRODUCT_MODEL_IDS:
            assignments[task] = ModelAssignment("explicit", model_id).to_dict()
        else:
            mode = AUTO_MODEL if task == "task_chat" else INHERIT_MODEL
            assignments[task] = ModelAssignment(mode).to_dict()
        self.state[legacy_key] = model_id if model_id.lower() not in self.LEGACY_PRODUCT_MODEL_IDS else ""
        self.state["ollama_model_assignments"] = assignment_values(assignments)
        self._save_state()

    def get_ollama_chat_model(self):
        return self._get_ollama_model("task_chat")

    def set_ollama_chat_model(self, model_name: str):
        self._set_ollama_model("task_chat", "ollama_chat_model", model_name)

    def get_ollama_title_model(self):
        return self._get_ollama_model("task_title")

    def set_ollama_title_model(self, model_name: str):
        self._set_ollama_model("task_title", "ollama_title_model", model_name)

    def get_ollama_chart_model(self):
        return self._get_ollama_model("task_chart")

    def set_ollama_chart_model(self, model_name: str):
        self._set_ollama_model("task_chart", "ollama_chart_model", model_name)

    def get_ollama_web_validate_model(self):
        return self._get_ollama_model("task_web_validate")

    def set_ollama_web_validate_model(self, model_name: str):
        self._set_ollama_model("task_web_validate", "ollama_web_validate_model", model_name)

    def get_ollama_web_summarize_model(self):
        return self._get_ollama_model("task_web_summarize")

    def set_ollama_web_summarize_model(self, model_name: str):
        self._set_ollama_model("task_web_summarize", "ollama_web_summarize_model", model_name)

    def get_ollama_reasoning_level(self):
        return self.state.get("ollama_reasoning_level", "high")

    def set_ollama_reasoning_level(self, level: str):
        if level in self.REASONING_LEVELS:
            self.state['ollama_reasoning_level'] = level
            self._save_state()

    def get_ollama_scanned_models(self):
        models = self.state.get("ollama_scanned_models", [])
        if not isinstance(models, list):
            return []
        return [str(model).strip() for model in models if str(model).strip()]

    def get_ollama_model_scan_mode(self):
        return str(self.state.get("ollama_model_scan_mode", "")).strip()

    def get_ollama_model_scan_path(self):
        return str(self.state.get("ollama_model_scan_path", "")).strip()

    def get_ollama_model_scan_locations(self):
        locations = self.state.get("ollama_model_scan_locations", [])
        if not isinstance(locations, list):
            return []
        return [str(location).strip() for location in locations if str(location).strip()]

    def set_ollama_model_scan_cache(self, models: list[str], scan_mode: str = "", scan_path: str = "", locations: list[str] | None = None):
        self.state["ollama_scanned_models"] = sorted(
            {str(model).strip() for model in (models or []) if str(model).strip()},
            key=str.lower,
        )
        self.state["ollama_model_scan_mode"] = str(scan_mode or "").strip()
        self.state["ollama_model_scan_path"] = str(scan_path or "").strip()
        self.state["ollama_model_scan_locations"] = sorted(
            {str(location).strip() for location in (locations or []) if str(location).strip()},
            key=str.lower,
        )
        self._save_state()

    def get_llama_cpp_chat_model_path(self):
        return str(self.state.get("llama_cpp_chat_model_path", "")).strip()

    def set_llama_cpp_chat_model_path(self, model_path: str):
        self.state["llama_cpp_chat_model_path"] = str(model_path or "").strip()
        self._save_state()

    def get_llama_cpp_title_model_path(self):
        title_model = str(self.state.get("llama_cpp_title_model_path", "")).strip()
        if title_model:
            return title_model
        return self.get_llama_cpp_chat_model_path()

    def get_llama_cpp_title_model_override_path(self):
        return str(self.state.get("llama_cpp_title_model_path", "")).strip()

    def set_llama_cpp_title_model_path(self, model_path: str):
        self.state["llama_cpp_title_model_path"] = str(model_path or "").strip()
        self._save_state()

    def get_llama_cpp_reasoning_level(self):
        return self.state.get("llama_cpp_reasoning_level", "high")

    def set_llama_cpp_reasoning_level(self, level: str):
        if level in self.REASONING_LEVELS:
            self.state['llama_cpp_reasoning_level'] = level
            self._save_state()

    def get_anthropic_reasoning_level(self):
        return self.state.get("anthropic_reasoning_level", "off")

    def set_anthropic_reasoning_level(self, level: str):
        if level in self.REASONING_LEVELS:
            self.state['anthropic_reasoning_level'] = level
            self._save_state()

    def get_gemini_reasoning_level(self):
        return self.state.get("gemini_reasoning_level", "off")

    def set_gemini_reasoning_level(self, level: str):
        if level in self.REASONING_LEVELS:
            self.state['gemini_reasoning_level'] = level
            self._save_state()

    def get_openai_reasoning_level(self):
        return self.state.get("openai_reasoning_level", "off")

    def set_openai_reasoning_level(self, level: str):
        if level in self.REASONING_LEVELS:
            self.state['openai_reasoning_level'] = level
            self._save_state()

    def get_llama_cpp_chat_format(self):
        return str(self.state.get("llama_cpp_chat_format", "")).strip()

    def set_llama_cpp_chat_format(self, chat_format: str):
        self.state["llama_cpp_chat_format"] = str(chat_format or "").strip()
        self._save_state()

    def get_llama_cpp_n_ctx(self):
        try:
            return int(self.state.get("llama_cpp_n_ctx", 4096))
        except (TypeError, ValueError):
            return 4096

    def get_llama_cpp_n_gpu_layers(self):
        try:
            return int(self.state.get("llama_cpp_n_gpu_layers", 0))
        except (TypeError, ValueError):
            return 0

    def get_llama_cpp_n_threads(self):
        try:
            return int(self.state.get("llama_cpp_n_threads", 0))
        except (TypeError, ValueError):
            return 0

    def get_llama_cpp_scanned_models(self):
        models = self.state.get("llama_cpp_scanned_models", [])
        if not isinstance(models, list):
            return []
        return [
            str(model).strip()
            for model in models
            if _is_llama_cpp_gguf_path(model)
        ]

    def get_llama_cpp_model_scan_mode(self):
        return str(self.state.get("llama_cpp_model_scan_mode", "")).strip()

    def get_llama_cpp_model_scan_path(self):
        return str(self.state.get("llama_cpp_model_scan_path", "")).strip()

    def get_llama_cpp_model_scan_locations(self):
        locations = self.state.get("llama_cpp_model_scan_locations", [])
        if not isinstance(locations, list):
            return []
        return [str(location).strip() for location in locations if str(location).strip()]

    def set_llama_cpp_runtime(self, *, n_ctx: int, n_gpu_layers: int, n_threads: int, chat_format: str):
        self.state["llama_cpp_n_ctx"] = max(256, int(n_ctx))
        self.state["llama_cpp_n_gpu_layers"] = int(n_gpu_layers)
        self.state["llama_cpp_n_threads"] = max(0, int(n_threads))
        self.state["llama_cpp_chat_format"] = str(chat_format or "").strip()
        self._save_state()

    def set_llama_cpp_model_scan_cache(self, models: list[str], scan_mode: str = "", scan_path: str = "", locations: list[str] | None = None):
        self.state["llama_cpp_scanned_models"] = sorted(
            {
                str(model).strip()
                for model in (models or [])
                if _is_llama_cpp_gguf_path(model)
            },
            key=str.lower,
        )
        self.state["llama_cpp_model_scan_mode"] = str(scan_mode or "").strip()
        self.state["llama_cpp_model_scan_path"] = str(scan_path or "").strip()
        self.state["llama_cpp_model_scan_locations"] = sorted(
            {str(location).strip() for location in (locations or []) if str(location).strip()},
            key=str.lower,
        )
        self._save_state()

    def get_llama_cpp_settings(self):
        return {
            "chat_model_path": self.get_llama_cpp_chat_model_path(),
            "title_model_path": self.get_llama_cpp_title_model_override_path(),
            "reasoning_level": self.get_llama_cpp_reasoning_level(),
            "chat_format": self.get_llama_cpp_chat_format(),
            "n_ctx": self.get_llama_cpp_n_ctx(),
            "n_gpu_layers": self.get_llama_cpp_n_gpu_layers(),
            "n_threads": self.get_llama_cpp_n_threads(),
        }
            
    def get_current_mode(self):
        return self.state.get("current_mode", "Ollama (Local)")

    def set_current_mode(self, mode: str):
        self.state["current_mode"] = mode
        self._save_state()

    def get_api_provider(self):
        return self.state.get("api_provider", "OpenAI-Compatible")
        
    def get_api_base_url(self):
        return self.state.get("api_base_url", "https://api.openai.com/v1")
        
    def get_openai_key(self):
        return graphlink_secrets.unprotect(self.state.get("openai_api_key", ""))

    def get_anthropic_key(self):
        return graphlink_secrets.unprotect(self.state.get("anthropic_api_key", ""))

    def get_gemini_key(self):
        return graphlink_secrets.unprotect(self.state.get("gemini_api_key", ""))

    def get_github_token(self):
        return graphlink_secrets.unprotect(self.state.get("github_access_token", ""))
        
    def get_api_models(self, provider: str | None = None):
        provider = provider or self.get_api_provider()
        profiles = self.state.get("api_models_by_provider", {})
        if isinstance(profiles, dict):
            return dict(profiles.get(provider, {}) or {})
        return dict(self.state.get("api_models", {}) or {})

    def get_api_model_catalog(self, provider: str | None = None):
        """Return the last successful provider catalog refresh for the UI."""
        provider = provider or self.get_api_provider()
        catalogs = self.state.get("api_model_catalog_by_provider", {})
        raw_models = catalogs.get(provider, []) if isinstance(catalogs, dict) else []
        if not isinstance(raw_models, list):
            return []

        normalized = []
        seen = set()
        for raw_model in raw_models:
            if isinstance(raw_model, dict):
                model_id = str(raw_model.get("model_id") or raw_model.get("id") or "").strip()
                descriptor = dict(raw_model)
            else:
                model_id = str(raw_model or "").strip()
                descriptor = {}
            if not model_id or model_id.lower() in seen:
                continue
            seen.add(model_id.lower())
            descriptor.update(
                {
                    "model_id": model_id,
                    "provider": str(descriptor.get("provider") or provider),
                    "ready": bool(descriptor.get("ready", True)),
                    "available": bool(descriptor.get("available", True)),
                    "capabilities": sorted(
                        {str(item).strip() for item in descriptor.get("capabilities", []) if str(item).strip()}
                    ),
                }
            )
            normalized.append(descriptor)
        return normalized

    def set_api_settings(
        self,
        provider: str,
        base_url: str,
        openai_key: str,
        anthropic_key: str,
        gemini_key: str,
    ):
        self.state["api_provider"] = provider
        self.state["api_base_url"] = base_url
        self.state["openai_api_key"] = self._protect_and_track(openai_key)
        self.state["anthropic_api_key"] = self._protect_and_track(anthropic_key)
        self.state["gemini_api_key"] = self._protect_and_track(gemini_key)
        self._save_state()

    def set_api_models(self, models_dict: dict, provider: str | None = None):
        provider = provider or self.get_api_provider()
        normalized = {
            str(task): normalize_model_id(model)
            for task, model in (models_dict or {}).items()
            if normalize_model_id(model)
        }
        profiles = self.state.get("api_models_by_provider", {})
        if not isinstance(profiles, dict):
            profiles = {}
        profiles[provider] = normalized
        self.state["api_models_by_provider"] = profiles
        self.state["api_models"] = normalized
        self._save_state()

    def set_api_model_catalog(self, models: list[dict] | list[str], provider: str | None = None):
        """Persist a normalized, non-secret snapshot of a provider model catalog."""
        provider = provider or self.get_api_provider()
        normalized = []
        seen = set()
        for raw_model in models or []:
            if isinstance(raw_model, dict):
                model_id = str(raw_model.get("model_id") or raw_model.get("id") or "").strip()
                descriptor = dict(raw_model)
            else:
                model_id = str(raw_model or "").strip()
                descriptor = {}
            if not model_id or model_id.lower() in seen:
                continue
            seen.add(model_id.lower())
            normalized.append(
                {
                    "model_id": model_id,
                    "provider": str(descriptor.get("provider") or provider),
                    "ready": bool(descriptor.get("ready", True)),
                    "available": bool(descriptor.get("available", True)),
                    "capabilities": sorted(
                        {str(item).strip() for item in descriptor.get("capabilities", []) if str(item).strip()}
                    ),
                }
            )
        catalogs = self.state.get("api_model_catalog_by_provider", {})
        if not isinstance(catalogs, dict):
            catalogs = {}
        catalogs[provider] = normalized
        self.state["api_model_catalog_by_provider"] = catalogs
        self._save_state()

    def set_github_token(self, token: str):
        self.state["github_access_token"] = self._protect_and_track(token)
        self._save_state()

    def reset_api_settings(self):
        self.state["api_provider"] = "OpenAI-Compatible"
        self.state["api_base_url"] = "https://api.openai.com/v1"
        self.state["openai_api_key"] = ""
        self.state["anthropic_api_key"] = ""
        self.state["gemini_api_key"] = ""
        self.state["api_models"] = {}
        self.state["api_models_by_provider"] = {}
        self._save_state()

    def get_mcp_servers(self) -> list:
        """ADR-007 stage 7.5: configured MCP servers - the persisted
        counterpart of backend/mcp_client.py's McpServerConfig (plain JSON-
        safe dicts here; that module owns converting to/from its own
        dataclass via to_dict/from_dict, keeping this store agnostic of
        that module's types, same posture as get_llama_cpp_settings'
        plain-dict return). Malformed/legacy entries are dropped rather
        than raised on - a corrupted single entry must never break every
        other configured server, or settings loading itself."""
        raw_servers = self.state.get("mcp_servers", [])
        if not isinstance(raw_servers, list):
            return []
        servers = []
        for entry in raw_servers:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            command = str(entry.get("command", "")).strip()
            if not name or not command:
                continue
            servers.append({
                "name": name,
                "command": command,
                "args": [str(a) for a in (entry.get("args") or [])],
                "scopes": sorted({str(s) for s in (entry.get("scopes") or [])}),
                "approval": str(entry.get("approval") or "always"),
                "enabled_tools": sorted({str(t) for t in (entry.get("enabled_tools") or [])}),
                "enabled": bool(entry.get("enabled", True)),
                "timeout": float(entry.get("timeout", 30.0) or 30.0),
            })
        return servers

    def set_mcp_servers(self, servers: list) -> None:
        """Replaces the WHOLE configured-server list, same "replace the
        collection" posture as set_ollama_model_assignments - this is a
        small, user-managed list edited as a unit (add/remove/edit one
        server via a settings panel - ADR-012's own future surface, see
        backend/mcp_client.py's module docstring), not an incrementally-
        patched map. Validates the same way get_mcp_servers reads back
        (name/command required, everything else normalized/defaulted) so
        a round trip through set then get is always well-formed."""
        normalized = []
        for entry in (servers or []):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            command = str(entry.get("command", "")).strip()
            if not name or not command:
                continue
            normalized.append({
                "name": name,
                "command": command,
                "args": [str(a) for a in (entry.get("args") or [])],
                "scopes": sorted({str(s) for s in (entry.get("scopes") or [])}),
                "approval": str(entry.get("approval") or "always"),
                "enabled_tools": sorted({str(t) for t in (entry.get("enabled_tools") or [])}),
                "enabled": bool(entry.get("enabled", True)),
                "timeout": float(entry.get("timeout", 30.0) or 30.0),
            })
        self.state["mcp_servers"] = normalized
        self._save_state()
