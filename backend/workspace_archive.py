"""ADR-009 stage 9.4: the `.graphlink` workspace archive (export / import).

FORMAT. A plain zip, deliberately readable without this app:

    manifest.json              format version, app version, export time, index
    chats/<n>.json             one chat: {title, data, notes, pins}
    assets/<ref>.<ext>         binary assets, real files, content-addressed

Chats are JSON and assets are files precisely because the point is
portability: someone should be able to unzip this, read their own
conversations in a text editor, and recover their images without running
anything. Base64-in-a-blob would technically round-trip and defeat that.

IMPORT IS INERT. Reading an archive creates chat rows and asset files.
Nothing in an archive can name a file outside the extraction target, cause
code to run, or reach the network. Every entry name is validated before
use (see _safe_member_name) because a zip is an untrusted input even when
the user believes they authored it - the classic zip-slip is `../../` in a
member name, and Python's zipfile does not stop you from honouring it.
Execution-bearing nodes carried in an archive arrive in exactly the state
any other loaded chat would: their ADR-005 approval gates still apply,
because import writes chat rows and never touches approval state.

EXPORT IS SCRUBBED. Every chat payload passes through
backend/secret_scrub.scrub() on the way out - that function, not this
module, is the single place that decides what counts as a secret (stage
9.3). An export is the primary way data leaves this machine, so it is the
primary reason that chokepoint exists.

VERSIONING. `formatVersion` is checked on import and refused if it is
newer than this build understands, rather than being read optimistically
and mangled. Same posture as ADR-003's wire-protocol negotiation: refuse
clearly instead of half-succeeding.
"""

from __future__ import annotations

import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from graphlink_version import APP_VERSION

from backend import asset_store as asset_store_module
from backend.asset_store import AssetStore, extension_for_mime
from backend.secret_scrub import scrub

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
CHATS_PREFIX = "chats/"
ASSETS_PREFIX = "assets/"

# A single archive member that unpacks to more than this is refused rather
# than written. Guards the decompression-bomb case: a few KB of zip can
# expand to gigabytes, and an import that fills the user's disk is a real
# denial of service even from a file they trusted.
MAX_MEMBER_BYTES = 256 * 1024 * 1024


class ArchiveError(Exception):
    """Any refusal to read an archive. Carries a message written for the
    user, since every raise site here ends up in a notification."""


def _safe_member_name(name: str) -> str:
    """Rejects anything that could escape the extraction root.

    Zip member names are attacker-controlled data. `../../.ssh/authorized_keys`
    and `C:\\Windows\\System32\\...` are both legal strings in a zip, and
    both are honoured by a naive `open(target_dir / name, "wb")`. This
    refuses absolute paths, drive letters, UNC prefixes, and any parent
    traversal outright rather than trying to normalize them into safety."""
    if not name or name.endswith("/"):
        raise ArchiveError(f"archive contains an unnamed entry: {name!r}")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or ".." in normalized.split("/"):
        raise ArchiveError(f"archive entry escapes the archive: {name!r}")
    if len(normalized) > 1 and normalized[1] == ":":
        raise ArchiveError(f"archive entry is an absolute path: {name!r}")
    return normalized


def _collect_asset_refs(value: Any, found: set[str]) -> None:
    """Walks a chat payload for asset references written by stage 9.5's
    externalized form ({"asset_ref": ..., "mime_type": ...}). A payload
    still carrying inline bytes simply yields nothing here - export works
    on both shapes, which is what lets 9.5's migration be gradual."""
    if isinstance(value, dict):
        ref = value.get("asset_ref")
        if isinstance(ref, str) and ref:
            found.add(ref)
        for item in value.values():
            _collect_asset_refs(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_asset_refs(item, found)


def _mime_by_ref(value: Any, mapping: dict[str, str]) -> None:
    if isinstance(value, dict):
        ref = value.get("asset_ref")
        if isinstance(ref, str) and ref:
            mapping[ref] = str(value.get("mime_type") or "")
        for item in value.values():
            _mime_by_ref(item, mapping)
    elif isinstance(value, list):
        for item in value:
            _mime_by_ref(item, mapping)


def export_archive(
    archive_path: Path,
    chats: list[dict[str, Any]],
    *,
    live_assets: AssetStore | None = None,
) -> Path:
    """Writes `chats` to `archive_path` as a `.graphlink` archive.

    Each entry in `chats` is {"title", "data", "notes", "pins"} - the exact
    shape backend/chat_library.py's own load_chat_row/load_notes_rows/
    load_pins_rows already return, so the caller does no reshaping.

    Every payload is scrubbed on the way in. Assets are copied out of
    `live_assets` as real files; a ref the store has never seen is skipped
    with a warning rather than aborting the whole export - one missing
    picture must not cost the user every conversation in the archive."""
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = archive_path.with_name(archive_path.name + ".tmp")

    index: list[dict[str, Any]] = []
    written_refs: set[str] = set()

    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for position, chat in enumerate(chats):
                payload = {
                    "title": chat.get("title") or "Untitled",
                    "data": scrub(chat.get("data") or {}),
                    "notes": scrub(chat.get("notes") or []),
                    "pins": scrub(chat.get("pins") or []),
                }
                member = f"{CHATS_PREFIX}{position}.json"
                archive.writestr(member, json.dumps(payload, indent=2))
                index.append({"member": member, "title": payload["title"]})

                if live_assets is None:
                    continue
                refs: set[str] = set()
                _collect_asset_refs(payload["data"], refs)
                mimes: dict[str, str] = {}
                _mime_by_ref(payload["data"], mimes)
                for ref in sorted(refs):
                    if ref in written_refs:
                        continue
                    data = live_assets.get(ref)
                    if data is None:
                        logger.warning("asset %s referenced but not in the store - skipping", ref)
                        continue
                    extension = extension_for_mime(mimes.get(ref))
                    archive.writestr(f"{ASSETS_PREFIX}{ref}.{extension}", data)
                    written_refs.add(ref)

            manifest = {
                "formatVersion": FORMAT_VERSION,
                "appVersion": APP_VERSION,
                "exportedAt": datetime.now(timezone.utc).isoformat(),
                "chatCount": len(index),
                "assetCount": len(written_refs),
                "chats": index,
            }
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    # Atomic publish, same discipline as db_backup/take_backup: a crash
    # mid-write leaves a .tmp, never a truncated file wearing the real
    # name that a user would reasonably believe is a complete backup.
    tmp_path.replace(archive_path)
    return archive_path


def read_archive(archive_path: Path) -> dict[str, Any]:
    """Parses and validates an archive WITHOUT writing anything anywhere.

    Split from import_archive on purpose: it makes the validation path
    testable in isolation, and it means a malformed archive is rejected
    before a single row or file has been created."""
    if not archive_path.is_file():
        raise ArchiveError(f"{archive_path.name} does not exist")

    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = [_safe_member_name(info.filename) for info in archive.infolist() if not info.is_dir()]
            for info in archive.infolist():
                if info.file_size > MAX_MEMBER_BYTES:
                    raise ArchiveError(
                        f"{archive_path.name} contains an entry larger than "
                        f"{MAX_MEMBER_BYTES // (1024 * 1024)} MB and was refused"
                    )
            if MANIFEST_NAME not in names:
                raise ArchiveError(f"{archive_path.name} is not a Graphlink archive (no manifest)")

            try:
                manifest = json.loads(archive.read(MANIFEST_NAME))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ArchiveError(f"{archive_path.name} has an unreadable manifest") from exc

            format_version = manifest.get("formatVersion")
            if not isinstance(format_version, int):
                raise ArchiveError(f"{archive_path.name} has no usable format version")
            if format_version > FORMAT_VERSION:
                raise ArchiveError(
                    f"{archive_path.name} was written by a newer version of Graphlink "
                    f"(format {format_version}, this build understands {FORMAT_VERSION}). "
                    "Update Graphlink and try again."
                )

            chats: list[dict[str, Any]] = []
            for name in sorted(n for n in names if n.startswith(CHATS_PREFIX)):
                try:
                    chats.append(json.loads(archive.read(name)))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ArchiveError(f"{archive_path.name} has an unreadable chat entry ({name})") from exc

            assets: dict[str, bytes] = {}
            for name in (n for n in names if n.startswith(ASSETS_PREFIX)):
                ref = Path(name).stem
                assets[ref] = archive.read(name)
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"{archive_path.name} is not a readable archive") from exc

    return {"manifest": manifest, "chats": chats, "assets": assets}


def import_archive(archive_path: Path, target_assets: AssetStore | None = None) -> list[dict[str, Any]]:
    """Reads an archive and returns its chats, having first materialized
    any assets it carries into `target_assets`.

    Assets are verified against their own content hash before being kept -
    a ref whose bytes do not hash to it is dropped with a warning rather
    than stored under a name that lies about its contents. Returns the
    chat payloads for the caller to write; this module never touches the
    database itself, which keeps the "what does importing mean" decision
    (new rows? merge? replace?) where it belongs."""
    parsed = read_archive(archive_path)

    if target_assets is not None:
        for ref, data in parsed["assets"].items():
            actual = asset_store_module.content_ref(data)
            if actual != ref:
                logger.warning(
                    "archive asset %s does not match its own content hash (%s) - dropping", ref, actual
                )
                continue
            target_assets.put(data)

    return parsed["chats"]
