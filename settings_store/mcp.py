"""Persisted MCP connections, with stable identities and write-only credentials.

Both stdio environment values and HTTP header values use the application's
existing secret-at-rest protection. A wire edit which omits credentials
preserves them by server ID, never by display name or list position.
"""

from __future__ import annotations

import math
import uuid
from urllib.parse import urlsplit

import graphlink_secrets
from settings_store._composed import SettingsManagerParts


def _normalize_mcp_env(raw):
    if not isinstance(raw, dict):
        return {}
    return {
        str(key).strip(): str(value)
        for key, value in raw.items()
        if str(key).strip() and value is not None
    }


def _string_list(raw) -> list[str]:
    return [str(value) for value in raw] if isinstance(raw, (list, tuple, set, frozenset)) else []


def _connection_fields(entry: dict) -> dict | None:
    name = str(entry.get("name") or "").strip()
    transport = str(entry.get("transport") or "stdio").strip()
    command = str(entry.get("command") or "").strip()
    url = str(entry.get("url") or "").strip()
    if not name or transport not in {"stdio", "streamable_http"}:
        return None
    if transport == "stdio" and not command or transport == "streamable_http" and not url:
        return None
    try:
        timeout = float(entry.get("timeout", 30.0) or 30.0)
    except (ValueError, TypeError):
        timeout = 30.0
    if not math.isfinite(timeout) or not 1 <= timeout <= 300:
        timeout = 30.0
    approval = str(entry.get("approval") or "always")
    if approval not in {"always", "once", "auto"}:
        approval = "always"
    return {
        "name": name, "transport": transport, "command": command, "url": url,
        "args": _string_list(entry.get("args")),
        "scopes": sorted(set(_string_list(entry.get("scopes")))),
        "approval": approval,
        "enabled_tools": sorted(set(_string_list(entry.get("enabled_tools")))),
        "enabled": bool(entry.get("enabled", True)), "timeout": timeout,
    }


def _origin(url: str) -> tuple | None:
    try:
        parsed = urlsplit(url)
        return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


class McpSettingsOps(SettingsManagerParts):
    def get_mcp_servers(self) -> list:
        raw_servers = self.state.get("mcp_servers", [])
        if not isinstance(raw_servers, list):
            return []
        servers = []
        seen: set[str] = set()
        backfilled = False
        for entry in raw_servers:
            if not isinstance(entry, dict):
                continue
            fields = _connection_fields(entry)
            if fields is None:
                continue
            entry_id = str(entry.get("id") or "").strip()
            if not entry_id or entry_id in seen:
                entry_id = uuid.uuid4().hex
                entry["id"] = entry_id
                backfilled = True
            seen.add(entry_id)
            servers.append({
                "id": entry_id, **fields,
                "env": self._unprotect_mcp_env(_normalize_mcp_env(entry.get("env"))),
                "headers": self._unprotect_mcp_env(_normalize_mcp_env(entry.get("headers"))),
            })
        if backfilled:
            self.state["mcp_servers"] = raw_servers
            self._save_state()
        return servers

    def set_mcp_servers(self, servers: list) -> None:
        """Replace the connection list, preserving omitted credentials by ID.

        An HTTP origin change clears omitted headers: a bearer token for one
        service must not silently be carried to a different service. Explicit
        replacement headers are always the user's new credential choice.
        """
        stored_servers = self.state.get("mcp_servers")
        if not isinstance(stored_servers, list):
            stored_servers = []
        existing = {
            str(entry["id"]): entry for entry in stored_servers
            if isinstance(entry, dict) and entry.get("id")
        }
        normalized = []
        seen: set[str] = set()
        for entry in servers if isinstance(servers, list) else []:
            if not isinstance(entry, dict):
                continue
            fields = _connection_fields(entry)
            if fields is None:
                continue
            entry_id = str(entry.get("id") or "").strip()
            if not entry_id or entry_id in seen:
                entry_id = uuid.uuid4().hex
            seen.add(entry_id)
            prior = existing.get(entry_id, {})
            saved = {"id": entry_id, **fields}
            for secret_field in ("env", "headers"):
                if secret_field in entry:
                    saved[secret_field] = self._protect_mcp_env(_normalize_mcp_env(entry[secret_field]))
                elif secret_field == "headers" and (
                    fields["transport"] != "streamable_http"
                    or _origin(fields["url"]) != _origin(str(prior.get("url") or ""))
                ):
                    saved[secret_field] = {}
                else:
                    saved[secret_field] = _normalize_mcp_env(prior.get(secret_field))
            normalized.append(saved)
        self.state["mcp_servers"] = normalized
        self._save_state()

    def _protect_mcp_env(self, env: dict) -> dict:
        """Protect values using DPAPI where available, the existing OS fallback elsewhere."""
        return {str(key): graphlink_secrets.protect(str(value)) for key, value in (env or {}).items()}

    def _unprotect_mcp_env(self, env: dict) -> dict:
        return {str(key): graphlink_secrets.unprotect(str(value)) for key, value in (env or {}).items()}
