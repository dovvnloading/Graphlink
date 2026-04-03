from datetime import datetime, timezone
import re

import requests
from PySide6.QtCore import QThread, Signal
from graphite_version import APP_VERSION

UPDATE_SIGNAL_URL = "https://raw.githubusercontent.com/dovvnloading/Graphlink/main/update_signal.md"
UPDATE_REPOSITORY_URL = "https://github.com/dovvnloading/Graphlink"

_VERSION_PATTERN = re.compile(r"^\s*v?(\d+(?:\.\d+)*)\s*$", re.IGNORECASE)


def parse_version_tuple(version_text: str):
    match = _VERSION_PATTERN.match(str(version_text or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def compare_versions(current_version: str, remote_version: str):
    current_parts = parse_version_tuple(current_version)
    remote_parts = parse_version_tuple(remote_version)
    if current_parts is None or remote_parts is None:
        normalized_current = str(current_version or "").strip()
        normalized_remote = str(remote_version or "").strip()
        if normalized_current == normalized_remote:
            return 0
        return 1 if normalized_remote > normalized_current else -1

    width = max(len(current_parts), len(remote_parts))
    current_padded = current_parts + (0,) * (width - len(current_parts))
    remote_padded = remote_parts + (0,) * (width - len(remote_parts))
    if remote_padded > current_padded:
        return 1
    if remote_padded < current_padded:
        return -1
    return 0


def build_update_result(current_version: str, remote_version: str):
    normalized_current = str(current_version or "").strip()
    normalized_remote = str(remote_version or "").strip()

    if not normalized_remote:
        return {
            "success": False,
            "update_available": False,
            "current_version": normalized_current,
            "remote_version": "",
            "message": "Update check failed because the remote version signal was empty.",
            "level": "error",
        }

    comparison = compare_versions(normalized_current, normalized_remote)
    if comparison > 0:
        return {
            "success": True,
            "update_available": True,
            "current_version": normalized_current,
            "remote_version": normalized_remote,
            "message": f"Update available: {normalized_remote} is ready. You're on {normalized_current}.",
            "level": "warning",
        }
    if comparison < 0:
        return {
            "success": True,
            "update_available": False,
            "current_version": normalized_current,
            "remote_version": normalized_remote,
            "message": (
                f"This build ({normalized_current}) is newer than the GitHub update signal "
                f"({normalized_remote})."
            ),
            "level": "info",
        }
    return {
        "success": True,
        "update_available": False,
        "current_version": normalized_current,
        "remote_version": normalized_remote,
        "message": f"You're up to date on {normalized_current}.",
        "level": "success",
    }


def timestamp_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class UpdateCheckWorker(QThread):
    finished_check = Signal(dict)

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self.current_version = str(current_version or "").strip()

    def run(self):
        try:
            response = requests.get(UPDATE_SIGNAL_URL, timeout=10)
            response.raise_for_status()
            result = build_update_result(self.current_version, response.text)
        except requests.RequestException as exc:
            result = {
                "success": False,
                "update_available": False,
                "current_version": self.current_version,
                "remote_version": "",
                "message": f"Update check failed: {exc}",
                "level": "error",
            }

        result["checked_at"] = timestamp_utc_iso()
        self.finished_check.emit(result)
