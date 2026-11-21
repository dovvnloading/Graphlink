"""One Ollama model inventory for all local-model settings consumers.

The Ollama SDK methods here call /api/tags, /api/show and /api/ps. A
successful tags response is authoritative for installed models; the existing
manifest-folder scanner is used only when the daemon is unavailable (or when
an explicit folder was requested). Incomplete show/ps metadata is *unknown*,
not evidence that a model lacks a capability or is unloaded.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import shutil
import subprocess
import sys

import ollama

from backend.ollama_context import trained_context_length
from backend.ollama_model_names import valid_ollama_model_name


_CAPABILITIES = {
    "completion": "chat",
    "chat": "chat",
    "tools": "tools",
    "vision": "vision",
    "embedding": "embeddings",
    "embeddings": "embeddings",
    "thinking": "thinking",
}


def _installed_ollama_command() -> list[str] | None:
    """Resolve only a local, already-installed Ollama app or executable.

    A model manifest is not evidence that the application is installed.
    Prefer desktop apps where they exist; otherwise use the installed CLI
    to serve requests. Never run an installer, downloader or shell command.
    """
    if sys.platform == "darwin":
        for app in (Path("/Applications/Ollama.app"), Path.home() / "Applications" / "Ollama.app"):
            if app.is_dir():
                return ["/usr/bin/open", str(app)]
    elif sys.platform == "win32":
        for variable in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            root = os.environ.get(variable)
            if not root:
                continue
            folder = Path(root) / ("Programs/Ollama" if variable == "LOCALAPPDATA" else "Ollama")
            for name in ("ollama app.exe", "Ollama.exe", "ollama.exe"):
                executable = folder / name
                if executable.is_file():
                    return [str(executable)] if name == "ollama app.exe" else [str(executable), "serve"]

    # Never pass a relative or non-executable PATH hit to Popen: an
    # attacker-controlled working directory must not become an app install.
    cli = shutil.which("ollama.exe" if sys.platform == "win32" else "ollama")
    if cli:
        executable = Path(cli)
        if executable.is_absolute() and executable.is_file() and (
            sys.platform == "win32" or os.access(executable, os.X_OK)
        ):
            return [str(executable.resolve()), "serve"]
    if sys.platform != "win32":
        for executable in (Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama"), Path.home() / ".local/bin/ollama"):
            if executable.is_file() and os.access(executable, os.X_OK):
                return [str(executable.resolve()), "serve"]
    return None


def _installed_ollama_serve_command() -> list[str] | None:
    """Find the installed Windows CLI even when the tray app takes precedence.

    Windows installers commonly place both executables in Programs/Ollama.
    Only an absolute executable in an installed location or PATH is accepted.
    """
    if sys.platform != "win32":
        return None
    for variable in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        root = os.environ.get(variable)
        if not root:
            continue
        folder = Path(root) / ("Programs/Ollama" if variable == "LOCALAPPDATA" else "Ollama")
        executable = folder / "ollama.exe"
        if executable.is_file():
            return [str(executable), "serve"]
    cli = shutil.which("ollama.exe")
    if cli:
        executable = Path(cli)
        if executable.is_absolute() and executable.is_file():
            return [str(executable.resolve()), "serve"]
    return None


def _status_tags() -> object:
    # The Ollama module-level client has no HTTP timeout. A stalled /api/tags
    # must not hold a Settings health request (or a Start probe) indefinitely.
    with ollama.Client(timeout=2.0) as client:
        return client.list()


def check_ollama_server_status() -> str:
    """Lightweight, time-bounded status; reject malformed /api/tags as unhealthy."""
    try:
        _tag_models(_status_tags())
    except Exception:
        return "installed" if _installed_ollama_command() is not None else "not_found"
    return "running"


def _spawn_installed_ollama(command: list[str]) -> None:
    options = dict(
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, shell=False,
    )
    if sys.platform == "win32":
        # A GUI app stays a GUI app; the CLI server runs without a console.
        if command[-1] == "serve":
            options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, **options)


def start_installed_ollama() -> bool:
    """Start an already-installed app or CLI; never install/download Ollama.

    Returns False if the daemon was already running. A failed spawn propagates
    to Settings so the user receives an actionable error.
    """
    if check_ollama_server_status() == "running":
        return False
    command = _installed_ollama_command()
    if command is None:
        raise FileNotFoundError("Ollama is not installed on this computer.")
    _spawn_installed_ollama(command)
    return True


def start_installed_ollama_server_fallback() -> bool:
    """Try Windows ollama.exe serve if the preferred tray app did not start a server.

    Never launch a second CLI when the original command already used serve.
    """
    preferred = _installed_ollama_command()
    if sys.platform != "win32" or preferred is None or preferred[-1] == "serve":
        return False
    command = _installed_ollama_serve_command()
    if command is None or check_ollama_server_status() == "running":
        return False
    _spawn_installed_ollama(command)
    return True


def _field(value: object, name: str, default=None):
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _nonnegative_int(value: object) -> int | None:
    # Preserve only precise, JSON-safe byte counts. A float, infinity or
    # giant numeric string from a daemon must not reach the JS memory UI.
    if isinstance(value, int) and not isinstance(value, bool):
        # The Ollama SDK wraps /api/tags and /api/ps sizes in Pydantic
        # ByteSize (an int subclass). Normalize them before JSON output.
        number = int(value)
    elif isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 16:
        number = int(value)
    else:
        return None
    return number if 0 <= number <= 2**53 - 1 else None


def _name(value: object) -> str:
    name = _field(value, "model") or _field(value, "name")
    return name if valid_ollama_model_name(name) else ""


def _tag_models(tags: object) -> list | tuple:
    models = _field(tags, "models")
    if not isinstance(models, (list, tuple)) or len(models) > 2048:
        raise ValueError("Ollama returned a malformed or oversized /api/tags inventory.")
    return models


def _label(value: object) -> str:
    return value[:120] if isinstance(value, str) and value.isprintable() else ""


def _key(name: str) -> str:
    normalized = name.strip().casefold()
    return normalized if ":" in normalized else f"{normalized}:latest"


def _capabilities(show: object) -> list[str]:
    raw = _field(show, "capabilities")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return sorted({_CAPABILITIES[value.lower().strip()] for value in raw[:32]
                   if isinstance(value, str) and value.lower().strip() in _CAPABILITIES})


def _trained_context_length(show: object) -> int | None:
    """Read a bounded trained limit, never the served Modelfile num_ctx."""
    info = _field(show, "model_info") or _field(show, "modelinfo")
    return trained_context_length(info)


def _inventory_record(
    name: str, *, size: object = None, details: object = None,
    show: object = None, loaded: bool | None = None, memory: object = None,
    vram: object = None, source: str,
) -> dict:
    details = details or {}
    show_details = _field(show, "details") if show is not None else None
    return {
        "name": name,
        "sizeBytes": _nonnegative_int(size),
        "family": _label(_field(show_details, "family") or _field(details, "family")),
        "parameterSize": _label(_field(show_details, "parameter_size") or _field(details, "parameter_size")),
        "quantization": _label(_field(show_details, "quantization_level") or _field(details, "quantization_level")),
        "capabilities": _capabilities(show) if show is not None else [],
        "trainedContextLength": _trained_context_length(show) if show is not None else None,
        "loaded": loaded,
        "memoryBytes": _nonnegative_int(memory),
        "vramBytes": _nonnegative_int(vram),
        "source": source,
    }


def _folder_inventory(scan_path: str | None, *, server_error: str = "") -> dict:
    # Keep the existing manifest-root coverage (including OLLAMA_MODELS,
    # Windows LocalAppData, and user-selected folders) in exactly one place.
    import api_provider

    scan = api_provider.scan_local_ollama_models(scan_path)
    descriptors = {
        str(item.get("model_id", "")).casefold(): item
        for item in scan.get("descriptors", [])
        if isinstance(item, dict) and valid_ollama_model_name(item.get("model_id"))
    }
    names = sorted({name for name in scan.get("models", []) if valid_ollama_model_name(name)}, key=str.casefold)
    records = []
    for name in names:
        descriptor = descriptors.get(name.casefold(), {})
        record = _inventory_record(name, size=descriptor.get("size_bytes"), source="folder")
        record["quantization"] = _label(descriptor.get("quantization"))
        record["trainedContextLength"] = _nonnegative_int(descriptor.get("context_length"))
        records.append(record)
    return {
        "models": names,
        "inventory": records,
        "source": "folder",
        "scan_mode": scan.get("scan_mode", "folder" if scan_path else "system"),
        "scan_path": scan.get("scan_path", ""),
        "locations": scan.get("locations", []),
        "server_reachable": False if not scan_path else None,
        "server_error": server_error,
        "server_status": None if scan_path else (
            "installed" if _installed_ollama_command() is not None else "not_found"
        ),
    }


def _running_models() -> dict[str, object] | None:
    """Live /api/ps snapshot; None means unavailable, not that nothing is loaded."""
    try:
        models = _field(ollama.ps(), "models")
        if not isinstance(models, (list, tuple)) or len(models) > 2048:
            return None
    except Exception:
        return None
    return {_key(name): item for item in models if (name := _name(item))}


def refresh_ollama_loaded_state(inventory: list[dict]) -> list[dict]:
    """Update load state after a Check without re-fetching all /api/show rows.

    Each call represents one /api/ps snapshot. A failed request must replace
    stale load claims with 'unknown', not misreport 'Not loaded'.
    """
    running = _running_models()
    refreshed = []
    for row in inventory:
        if row.get("source") != "server":
            refreshed.append(dict(row))
            continue
        name = row.get("name")
        loaded_model = running.get(_key(name)) if running is not None and isinstance(name, str) else None
        refreshed.append({
            **row,
            "loaded": (loaded_model is not None) if running is not None else None,
            "memoryBytes": _nonnegative_int(_field(loaded_model, "size")),
            "vramBytes": _nonnegative_int(_field(loaded_model, "size_vram")),
        })
    return refreshed


def build_ollama_inventory(scan_path: str | None = None) -> dict:
    """Discover installed models, capabilities and loaded memory in one pass.

    This is a blocking I/O operation: Settings runs it via asyncio.to_thread.
    A failed show for one model or a failed ps does not hide other models.
    """
    if scan_path:
        return _folder_inventory(scan_path)

    try:
        tags = _tag_models(ollama.list())  # /api/tags must be a real bounded model list
    except Exception as exc:
        return _folder_inventory(None, server_error=str(exc))

    loaded_by_name = _running_models()
    records_by_name: dict[str, dict] = {}
    for item in tags:
        name = _name(item)
        if not name or _key(name) in records_by_name:
            continue
        try:
            show = ollama.show(name)  # /api/show
        except Exception:
            show = None  # A per-model failure must not discard the tags row.
        loaded_model = loaded_by_name.get(_key(name)) if loaded_by_name is not None else None
        records_by_name[_key(name)] = _inventory_record(
            name,
            size=_field(item, "size"),
            details=_field(item, "details"),
            show=show,
            loaded=(loaded_model is not None) if loaded_by_name is not None else None,
            memory=_field(loaded_model, "size"),
            vram=_field(loaded_model, "size_vram"),
            source="server",
        )

    records = sorted(records_by_name.values(), key=lambda row: row["name"].casefold())
    return {
        "models": [row["name"] for row in records],
        "inventory": records,
        "source": "server",
        "scan_mode": "server",
        "scan_path": "",
        "locations": [],
        "server_reachable": True,
        "server_error": "",
        "server_status": "running",
    }
