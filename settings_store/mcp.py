"""McpSettingsOps - MCP (Model Context Protocol) server configuration for
SettingsManager (ADR-007 stage 7.5): the persisted counterpart of
backend/mcp_client.py's McpServerConfig, including per-server env var
encryption at rest.

A MIXIN, not a standalone class: every method operates on the composing
class's own state (self.state) - it is composed exactly once, by
graphlink_settings_store.py's `class SettingsManager(PersistenceOps, ...,
McpSettingsOps, ...)`.

_normalize_mcp_env is a module-level helper, not a method, since it's only
ever called from within this module's own get/set methods.

Method bodies are relocated VERBATIM from graphlink_settings_store.py; only
the class wrapper is new.
"""

from __future__ import annotations

from settings_store._composed import SettingsManagerParts

import uuid

import graphlink_secrets


def _normalize_mcp_env(raw):
    """One MCP server's `env` as a clean str->str dict - tolerant of the
    shapes a hand-edited session.dat or an older payload can carry (missing,
    null, non-dict, non-string values, blank names). Malformed degrades to
    "no extra variables", never to a dropped server entry."""
    if not isinstance(raw, dict):
        return {}
    return {
        str(key).strip(): str(value)
        for key, value in raw.items()
        if str(key).strip() and value is not None
    }


class McpSettingsOps(SettingsManagerParts):

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
        backfilled = False
        for entry in raw_servers:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            command = str(entry.get("command", "")).strip()
            if not name or not command:
                continue
            # REVIEW-FIX: every entry needs a stable identity distinct from
            # `name` (two servers can share a name - nothing has ever
            # enforced uniqueness). Entries persisted before this field
            # existed get one backfilled here, in place, on the raw stored
            # dict - so it is written back below and never regenerated on a
            # later read, which would make it useless as a join key.
            entry_id = str(entry.get("id", "")).strip()
            if not entry_id:
                entry_id = uuid.uuid4().hex
                entry["id"] = entry_id
                backfilled = True
            servers.append({
                "id": entry_id,
                "name": name,
                "command": command,
                "args": [str(a) for a in (entry.get("args") or [])],
                "scopes": sorted({str(s) for s in (entry.get("scopes") or [])}),
                "approval": str(entry.get("approval") or "always"),
                "enabled_tools": sorted({str(t) for t in (entry.get("enabled_tools") or [])}),
                "enabled": bool(entry.get("enabled", True)),
                "timeout": float(entry.get("timeout", 30.0) or 30.0),
                # Absent on every entry persisted before the field existed -
                # reads back as "no extra variables", the same default the
                # write side stores. Decrypted here (values are encrypted at
                # rest by _protect_mcp_env) so the ONLY consumer that needs
                # the real values - the MCP server spawn in backend/agents.py
                # - gets them, while the settings wire payload never carries
                # them at all. Legacy plaintext entries pass through
                # unchanged.
                "env": self._unprotect_mcp_env(_normalize_mcp_env(entry.get("env"))),
            })
        if backfilled:
            self.state["mcp_servers"] = raw_servers
            self._save_state()
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
        # What is already stored, keyed by id (REVIEW-FIX: was keyed by
        # name), so an entry that arrives without an "env" key keeps its
        # configured variables rather than losing them - see the "env"
        # handling below. Keying by name meant two servers sharing a name
        # (nothing has ever enforced uniqueness) could silently swap or
        # merge each other's secrets the moment either was edited without
        # sending "env". A stored entry with no id yet (persisted before
        # this field existed) simply has nothing to preserve under - it
        # gets a fresh id below, same as a brand-new entry.
        preserved_env: dict[str, dict] = {}
        for stored in (self.state.get("mcp_servers") or []):
            if isinstance(stored, dict):
                stored_id = str(stored.get("id", "")).strip()
                if stored_id:
                    preserved_env[stored_id] = _normalize_mcp_env(stored.get("env"))
        normalized = []
        for entry in (servers or []):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            command = str(entry.get("command", "")).strip()
            if not name or not command:
                continue
            # An incoming entry echoes back whatever id an existing server
            # already has (round-tripped through the wire unchanged); one
            # with no id - a brand-new "Add Server" entry, or a legacy
            # stored entry that never had one - gets a fresh one assigned
            # here, server-side, never client-supplied.
            entry_id = str(entry.get("id", "")).strip() or uuid.uuid4().hex
            # REVIEW-FIX: this method's own docstring above promises "name/
            # command required, everything else normalized/defaulted" - but
            # "args" and "timeout" were previously built with no guard at
            # all: a non-iterable "args" (e.g. an int) raised TypeError out
            # of the list comprehension, and a non-numeric truthy "timeout"
            # (e.g. a string) raised ValueError out of float(). Either
            # exception propagated straight out of set_mcp_servers, aborting
            # the WHOLE bulk-replace and discarding every OTHER server's
            # valid edit in the same call - not just the one malformed
            # field on this one entry. Falling back to the same defaults
            # get_mcp_servers/this method already use for a missing value
            # keeps this entry (and every entry after it) instead.
            try:
                entry_args = [str(a) for a in (entry.get("args") or [])]
            except TypeError:
                entry_args = []
            try:
                entry_timeout = float(entry.get("timeout", 30.0) or 30.0)
            except (TypeError, ValueError):
                entry_timeout = 30.0
            normalized.append({
                "id": entry_id,
                "name": name,
                "command": command,
                "args": entry_args,
                "scopes": sorted({str(s) for s in (entry.get("scopes") or [])}),
                "approval": str(entry.get("approval") or "always"),
                "enabled_tools": sorted({str(t) for t in (entry.get("enabled_tools") or [])}),
                "enabled": bool(entry.get("enabled", True)),
                "timeout": entry_timeout,
                # Per-server environment variables - the only channel by
                # which a server process receives anything beyond the safe
                # allowlist base (see McpStdioClient.connect). These are real
                # user secrets (a GITHUB_TOKEN, a BRAVE_API_KEY), so each
                # VALUE is encrypted at rest exactly like the API keys this
                # store already holds - an earlier version of this comment
                # claimed the "same posture as the API keys" while in fact
                # writing them as plaintext JSON. Names stay in the clear:
                # the Settings page lists them, and a variable name is not
                # the secret.
                #
                # An entry that carries no "env" key at all means "leave
                # whatever is stored for this server alone" - the wire
                # deliberately never sends these values back (see
                # backend/settings.py's _mcp_servers_for_wire), so a
                # bulk-replace triggered by toggling one server's checkbox
                # would otherwise wipe every configured variable it could
                # not see.
                **(
                    {"env": self._protect_mcp_env(_normalize_mcp_env(entry.get("env")))}
                    if "env" in entry
                    else {"env": preserved_env.get(entry_id, {})}
                ),
            })
        self.state["mcp_servers"] = normalized
        self._save_state()

    def _protect_mcp_env(self, env: dict) -> dict:
        """Encrypt each env VALUE at rest, leaving names readable. Mirrors
        _protect_and_track's use of graphlink_secrets.protect for the
        top-level API keys, including its plaintext fallback when DPAPI is
        unavailable (see graphlink_secrets' own docstring - refusing to save
        would be worse than saving what this platform can protect)."""
        return {str(key): graphlink_secrets.protect(str(value)) for key, value in (env or {}).items()}

    def _unprotect_mcp_env(self, env: dict) -> dict:
        """The read side of _protect_mcp_env. Legacy plaintext values (written
        before env was encrypted) come back unchanged - graphlink_secrets.
        unprotect passes through anything without the "dpapi:" prefix, the
        same way the top-level secrets migrated."""
        return {str(key): graphlink_secrets.unprotect(str(value)) for key, value in (env or {}).items()}
