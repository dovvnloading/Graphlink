"""Filesystem discovery of local llama.cpp GGUF model files.

Function bodies are relocated VERBATIM from api_provider.py; only the
patch-seam rewrites below are new. Any name that lives in api_provider's
module namespace (module globals, sibling helpers, constants, and the
`ollama`/`urllib`/`requests` module bindings) is accessed late-bound as
`_mod.<name>` through an in-body deferred `import api_provider as _mod`,
NEVER via a module-top import here: a top-level `from api_provider import X`
would be a circular import (api_provider imports this module at ITS top)
AND would freeze the name at import time, making the test suite's
`monkeypatch.setattr(api_provider, "X", ...)` patches invisible to these
functions. The deferred-import-then-attribute pattern resolves the name on
api_provider at call time, so those patch seams keep working with zero test
changes. api_provider.py re-exports every name below, so every existing
`api_provider.<name>` caller and patch site is unchanged.
"""

from __future__ import annotations

from pathlib import Path
import os


def _normalize_llama_cpp_scan_root(path_value: str | Path | None) -> Path | None:
    normalized = str(path_value or "").strip()
    if not normalized:
        return None

    candidate = Path(normalized).expanduser()
    if candidate.is_file():
        return candidate.parent
    return candidate


def _iter_existing_llama_cpp_scan_roots() -> list[Path]:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    local_app_data = os.environ.get("LOCALAPPDATA")
    candidate_roots = [
        os.environ.get("LLAMA_CPP_MODELS"),
        Path.home() / "models",
        Path.home() / "llama.cpp",
        Path.home() / "llama.cpp" / "models",
        Path.home() / "Downloads",
        Path.home() / "Documents",
        Path.home() / "Desktop",
        Path.home() / ".cache" / "lm-studio" / "models",
        local_app_data and Path(local_app_data) / "llama.cpp" / "models",
    ]

    unique_roots: list[Path] = []
    seen_roots: set[str] = set()
    for raw_path in candidate_roots:
        root = _mod._normalize_llama_cpp_scan_root(raw_path)
        if not root or not root.is_dir():
            continue
        resolved = str(root.resolve()).lower()
        if resolved in seen_roots:
            continue
        seen_roots.add(resolved)
        unique_roots.append(root)
    return unique_roots


def _collect_gguf_files_from_root(root_path: Path) -> tuple[list[str], bool]:
    """Walk root_path for .gguf files, bounded by directory count and wall-clock time.

    The default (no scan_path configured) roots include the user's whole
    Downloads/Documents/Desktop trees (see _iter_existing_llama_cpp_scan_roots) - an
    unbounded os.walk there can run for a very long time against a pathological or
    cloud-synced tree. Returns (models, was_truncated) so callers can tell the scan
    stopped early rather than silently reporting an incomplete list as complete.
    """
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    discovered_models: set[str] = set()
    skip_directories = {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "node_modules",
        "venv",
        ".venv",
    }

    started_at = _mod.time.monotonic()
    directories_visited = 0
    truncated = False

    for current_root, dir_names, file_names in os.walk(root_path):
        directories_visited += 1
        if (
            directories_visited > _mod._GGUF_SCAN_MAX_DIRECTORIES
            or (_mod.time.monotonic() - started_at) > _mod._GGUF_SCAN_MAX_SECONDS
        ):
            truncated = True
            break

        dir_names[:] = [
            dir_name for dir_name in dir_names
            if dir_name.lower() not in skip_directories
        ]
        for file_name in file_names:
            if not file_name.lower().endswith(".gguf"):
                continue
            model_path = Path(current_root) / file_name
            discovered_models.add(str(model_path.resolve()))

    return sorted(discovered_models, key=str.lower), truncated


def scan_local_llama_cpp_models(scan_path: str | None = None) -> dict:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    if scan_path:
        root = _mod._normalize_llama_cpp_scan_root(scan_path)
        if not root or not root.exists():
            raise RuntimeError(f"Scan folder does not exist: {scan_path}")
        if not root.is_dir():
            raise RuntimeError(f"Scan folder is not a directory: {scan_path}")
        scan_roots = [root]
        scan_mode = "folder"
        scan_root = str(root.resolve())
    else:
        scan_roots = _mod._iter_existing_llama_cpp_scan_roots()
        scan_mode = "system"
        scan_root = ""

    discovered_models: set[str] = set()
    scanned_locations: list[str] = []
    truncated = False

    for root in scan_roots:
        root_models, root_truncated = _mod._collect_gguf_files_from_root(root)
        discovered_models.update(root_models)
        scanned_locations.append(str(root.resolve()))
        truncated = truncated or root_truncated

    return {
        "models": sorted(discovered_models, key=str.lower),
        "scan_mode": scan_mode,
        "scan_path": scan_root,
        "locations": sorted(set(scanned_locations), key=str.lower),
        "truncated": truncated,
    }
