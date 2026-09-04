"""Filesystem and daemon discovery of locally installed Ollama models.

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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graphlink_model_catalog import ModelDescriptor


def _normalize_ollama_models_root(path_value: str | Path | None) -> Path | None:
    normalized = str(path_value or "").strip()
    if not normalized:
        return None

    candidate = Path(normalized).expanduser()
    if not candidate.is_dir():
        return None

    # Support all common Ollama layout variants:
    #   .../manifests
    #   .../models
    #   .../<custom-root-with-manifests-and-blobs>
    candidate_name = candidate.name.lower()
    if candidate_name == "manifests":
        return candidate
    if (candidate / "manifests").is_dir():
        return candidate / "manifests"
    if (candidate / "models" / "manifests").is_dir():
        return candidate / "models" / "manifests"
    if candidate_name == "models":
        return candidate / "manifests"
    return candidate / "models" / "manifests"


def _iter_existing_ollama_manifest_roots() -> list[Path]:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    candidate_roots: list[Path] = []
    env_models_root = os.environ.get("OLLAMA_MODELS")
    local_app_data = os.environ.get("LOCALAPPDATA")
    program_data = os.environ.get("PROGRAMDATA")

    for raw_path in (
        env_models_root,
        Path.home() / ".ollama",
        Path.home() / ".ollama" / "models",
        local_app_data and Path(local_app_data) / "Ollama",
        local_app_data and Path(local_app_data) / "Ollama" / "models",
        program_data and Path(program_data) / "Ollama",
        program_data and Path(program_data) / "Ollama" / "models",
    ):
        manifests_root = _mod._normalize_ollama_models_root(raw_path)
        if manifests_root and manifests_root.is_dir():
            candidate_roots.append(manifests_root)

    unique_roots: list[Path] = []
    seen_roots: set[str] = set()
    for root in candidate_roots:
        resolved = str(root.resolve()).lower()
        if resolved in seen_roots:
            continue
        seen_roots.add(resolved)
        unique_roots.append(root)
    return unique_roots


def _discover_manifest_roots_in_folder(scan_path: str) -> list[Path]:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    root_path = Path(scan_path).expanduser()
    if not root_path.exists():
        raise RuntimeError(f"Scan folder does not exist: {scan_path}")
    if not root_path.is_dir():
        raise RuntimeError(f"Scan folder is not a directory: {scan_path}")

    direct_candidates = [
        root_path,
        root_path / "manifests",
        root_path / "models" / "manifests",
    ]
    manifest_roots: list[Path] = []
    seen_roots: set[str] = set()

    for candidate in direct_candidates:
        manifests_root = _mod._normalize_ollama_models_root(candidate)
        if manifests_root and manifests_root.is_dir():
            resolved = str(manifests_root.resolve()).lower()
            if resolved not in seen_roots:
                seen_roots.add(resolved)
                manifest_roots.append(manifests_root)

    for current_root, dir_names, _ in os.walk(root_path):
        current_name = os.path.basename(current_root).lower()
        parent_name = os.path.basename(os.path.dirname(current_root)).lower()
        if current_name == "blobs":
            dir_names[:] = []
            continue
        if current_name == "manifests" and parent_name == "models":
            manifests_root = Path(current_root)
            resolved = str(manifests_root.resolve()).lower()
            if resolved not in seen_roots:
                seen_roots.add(resolved)
                manifest_roots.append(manifests_root)
            dir_names[:] = []

    return manifest_roots


def _extract_model_name_from_manifest_path(manifest_path: Path, manifests_root: Path) -> str | None:
    try:
        relative_parts = manifest_path.relative_to(manifests_root).parts
    except ValueError:
        return None

    if len(relative_parts) < 3:
        return None

    repository_parts = list(relative_parts[1:-1])
    if repository_parts and repository_parts[0].lower() == "library":
        repository_parts = repository_parts[1:]
    if not repository_parts:
        return None

    tag = relative_parts[-1].strip()
    if not tag:
        return None

    repository_name = "/".join(part.strip() for part in repository_parts if part.strip())
    if not repository_name:
        return None

    return f"{repository_name}:{tag}"


def _collect_models_from_manifest_root(manifests_root: Path) -> list[str]:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    discovered_models: set[str] = set()
    for current_root, dir_names, file_names in os.walk(manifests_root):
        dir_names[:] = [dir_name for dir_name in dir_names if dir_name.lower() != "blobs"]
        for file_name in file_names:
            manifest_path = Path(current_root) / file_name
            model_name = _mod._extract_model_name_from_manifest_path(manifest_path, manifests_root)
            if model_name:
                discovered_models.add(model_name)
    return sorted(discovered_models, key=str.lower)


def _list_model_descriptors_from_running_ollama() -> tuple[list[ModelDescriptor], bool, str]:
    """Return installed Ollama models plus an honest server health signal."""
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    try:
        response = _mod.ollama.list()
    except Exception as exc:
        return [], False, str(exc)

    raw_models = _mod._extract_response_field(response, "models", [])
    descriptors = []
    for raw_model in raw_models or []:
        descriptor = _mod.ollama_descriptor(raw_model)
        if descriptor.model_id:
            descriptors.append(descriptor)
    return _mod.sort_descriptors(descriptors), True, ""


def scan_local_ollama_models(scan_path: str | None = None) -> dict:
    import api_provider as _mod  # deferred: patch-seam safety (see module docstring)
    running_descriptors: list[_mod.ModelDescriptor] = []
    server_reachable = None
    server_error = ""
    if scan_path:
        manifest_roots = _mod._discover_manifest_roots_in_folder(scan_path)
        scan_mode = "folder"
        scan_root = str(Path(scan_path).expanduser().resolve())
        running_models: list[str] = []
    else:
        manifest_roots = _mod._iter_existing_ollama_manifest_roots()
        scan_mode = "system"
        scan_root = ""
        running_descriptors, server_reachable, server_error = _mod._list_model_descriptors_from_running_ollama()
        running_models = [descriptor.model_id for descriptor in running_descriptors]

    discovered_models: set[str] = set(running_models)
    scanned_locations: list[str] = []

    for manifests_root in manifest_roots:
        discovered_models.update(_mod._collect_models_from_manifest_root(manifests_root))
        scanned_locations.append(str(manifests_root.resolve()))

    descriptors_by_id = {
        descriptor.model_id.lower(): descriptor
        for descriptor in (running_descriptors if not scan_path else [])
    }
    for model_name in discovered_models:
        descriptors_by_id.setdefault(
            model_name.lower(),
            _mod.ModelDescriptor(
                model_id=model_name,
                provider=_mod.config.LOCAL_PROVIDER_OLLAMA,
                ready=True,
                available=True,
                source="manifest",
            ),
        )

    return {
        "models": sorted(discovered_models, key=str.lower),
        "descriptors": [
            {
                "model_id": descriptor.model_id,
                "provider": descriptor.provider,
                "ready": descriptor.ready,
                "available": descriptor.available,
                "capabilities": sorted(descriptor.capabilities),
                "source": descriptor.source,
                "size_bytes": descriptor.size_bytes,
                "context_length": descriptor.context_length,
                "quantization": descriptor.quantization,
            }
            for descriptor in _mod.sort_descriptors(descriptors_by_id.values())
        ],
        "scan_mode": scan_mode,
        "scan_path": scan_root,
        "locations": sorted(set(scanned_locations), key=str.lower),
        "server_reachable": server_reachable if not scan_path else None,
        "server_error": server_error if not scan_path else "",
    }
