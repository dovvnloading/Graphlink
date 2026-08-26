from settings_store.cloud_provider import CloudProviderSettingsOps
from settings_store.general import GeneralSettingsOps
from settings_store.harness import HarnessSettingsOps
from settings_store.llama_cpp import LlamaCppSettingsOps
from settings_store.mcp import McpSettingsOps
from settings_store.ollama import OllamaSettingsOps
from settings_store.persistence import PersistenceOps
from settings_store.plugin_grants import PluginGrantsOps
from settings_store.pricing import PricingSettingsOps
from settings_store.recipes import RecipesOps


class _KeepExistingSecret:
    """Type of the KEEP_EXISTING_SECRET sentinel below - a class only so it
    has a readable repr in a traceback or a debugger."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<KEEP_EXISTING_SECRET>"


# Pass this instead of a value to set_api_settings to mean "leave whatever is
# already stored for this field exactly as it is".
#
# THE DATA LOSS THIS EXISTS TO PREVENT. Saving ONE provider's key used to
# round-trip the other two through decrypt-then-re-encrypt: the caller read
# them back with get_*_key() and handed the results straight to
# set_api_settings. But get_*_key() returns "" whenever DPAPI decryption
# FAILS, not only when no key is set - deliberately, on the read side - so a
# transient CryptUnprotectData failure (a temporary-profile logon, an
# unavailable master key on a domain machine, resource exhaustion; anything
# graphlink_secrets' own blanket except turns into None) at the moment the
# user saved their Anthropic key silently destroyed the still-recoverable
# OpenAI and Gemini blobs, with no warning at the time and no way back. The
# plaintext-migration path was hardened against exactly this hazard - it
# never touches an undecryptable blob - and this sentinel closes the same
# hole on the save path, by never reading those siblings at all rather than
# by trying to tell a failed decrypt apart from an empty one after the fact.
#
# An explicit "" still clears a key, so a user genuinely emptying the field
# is unaffected: the two intents are now distinguishable instead of collapsed
# into the same empty string.
KEEP_EXISTING_SECRET = _KeepExistingSecret()


class SettingsManager(
    PersistenceOps, GeneralSettingsOps, OllamaSettingsOps, LlamaCppSettingsOps,
    CloudProviderSettingsOps, McpSettingsOps, PluginGrantsOps, RecipesOps,
    HarnessSettingsOps, PricingSettingsOps,
):
    NOTIFICATION_TYPES = ("info", "success", "warning", "error")
    # R8a: the graded reasoning-effort vocabulary shared by all 5 providers'
    # reasoning-level fields below - see api_provider.py's own REASONING_LEVELS
    # docstring for the full per-provider mapping story this feeds.
    REASONING_LEVELS = ("off", "low", "medium", "high")
    # ADR-016 stage 16.1: the log-level setting's closed vocabulary - the
    # same names Python's logging module already uses, so
    # backend/observability.py.apply_log_level can pass this straight to
    # logging.getLevelName without a translation table.
    LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
    # ADR-012 stage 12.2: "system" defers to prefers-color-scheme (no
    # explicit [data-theme] stamped on <html> at all - see gl-vars-dev.css's
    # own cascade docstring); "light"/"dark" force that theme regardless of
    # OS preference. A prior theme setting existed and was removed for being
    # provably inert (commit 585d747, "R8a: remove the dead Theme setting") -
    # this one is a different, now-resolved problem: stage 12.1 gave the
    # frontend a real light palette and a real [data-theme] cascade to apply
    # this value to, so persisting it is no longer a no-op.
    THEMES = ("system", "light", "dark")
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
