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

    cli = shutil.which("ollama.exe" if sys.platform == "win32" else "ollama")
    if cli and Path(cli).is_file():
        return [cli, "serve"]
    if sys.platform != "win32":
        for executable in (Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama"), Path.home() / ".local/bin/ollama"):
            if executable.is_file():
                return [str(executable), "serve"]
    return None


def check_ollama_server_status() -> str:
    """Lightweight live status; the full model inventory is a separate scan."""
    try:
        ollama.list()  # The same /api/tags reachability signal as the shared inventory.
    except Exception:
        return "installed" if _installed_ollama_command() is not None else "not_found"
    return "running"


def start_installed_ollama() -> bool:
    """Start the installed application, never downloading or installing it.

    Returns False if the daemon was already running. A failed spawn propagates
    to the Settings intent so it can report an actionable error.
    """
    if check_ollama_server_status() == "running":
        return False
    command = _installed_ollama_command()
    if command is None:
        raise FileNotFoundError("Ollama is not installed on this computer.")

    options = dict(
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, shell=False,
    )
    if sys.platform == "win32":
        # Only a CLI server needs its console hidden; a GUI app stays a GUI app.
        if command[-1] == "serve":
            options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, **options)
    return True


def _field(value: object, name: str, default=None):
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number is not None and number >= 0 else None


def _name(value: object) -> str:
    return str(_field(value, "model") or _field(value, "name") or "").strip()


def _key(name: str) -> str:
    normalized = name.strip().casefold()
    return normalized if ":" in normalized else f"{normalized}:latest"


def _capabilities(show: object) -> list[str]:
    raw = _field(show, "capabilities") or []
    if isinstance(raw, str):
        raw = [raw]
    return sorted({_CAPABILITIES[str(value).lower().strip()] for value in raw
                   if str(value).lower().strip() in _CAPABILITIES})


def _trained_context_length(show: object) -> int | None:
    """Read the model's trained limit, never the separately configured num_ctx."""
    info = _field(show, "model_info") or _field(show, "modelinfo") or {}
    if not isinstance(info, Mapping):
        return None
    architecture = str(info.get("general.architecture") or "").strip()
    keys = ([f"{architecture}.context_length"] if architecture else [])
    keys.extend(str(key) for key in info if str(key).endswith(".context_length"))
    for key in keys:
        value = _nonnegative_int(info.get(key))
        if value is not None and value > 0:
            return value
    return None


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
        "family": str(_field(show_details, "family") or _field(details, "family") or ""),
        "parameterSize": str(_field(show_details, "parameter_size") or _field(details, "parameter_size") or ""),
        "quantization": str(_field(show_details, "quantization_level") or _field(details, "quantization_level") or ""),
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
        for item in scan.get("descriptors", []) if isinstance(item, dict)
    }
    names = sorted({str(name).strip() for name in scan.get("models", []) if str(name).strip()}, key=str.casefold)
    records = []
    for name in names:
        descriptor = descriptors.get(name.casefold(), {})
        record = _inventory_record(name, size=descriptor.get("size_bytes"), source="folder")
        record["quantization"] = str(descriptor.get("quantization") or "")
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


def build_ollama_inventory(scan_path: str | None = None) -> dict:
    """Discover installed models, capabilities and loaded memory in one pass.

    This is a blocking I/O operation: Settings runs it via asyncio.to_thread.
    A failed show for one model or a failed ps does not hide other models.
    """
    if scan_path:
        return _folder_inventory(scan_path)

    try:
        tags = ollama.list()  # /api/tags
    except Exception as exc:
        return _folder_inventory(None, server_error=str(exc))

    try:
        running = _field(ollama.ps(), "models", []) or []  # /api/ps
    except Exception:
        running = None  # Unknown, rather than claiming everything is unloaded.

    loaded_by_name = {_key(_name(item)): item for item in running if _name(item)} if running is not None else {}
    records_by_name: dict[str, dict] = {}
    for item in _field(tags, "models", []) or []:
        name = _name(item)
        if not name:
            continue
        try:
            show = ollama.show(name)  # /api/show
        except Exception:
            show = None  # A per-model failure must not discard the tags row.
        loaded_model = loaded_by_name.get(_key(name))
        records_by_name[_key(name)] = _inventory_record(
            name,
            size=_field(item, "size"),
            details=_field(item, "details"),
            show=show,
            loaded=(loaded_model is not None) if running is not None else None,
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
