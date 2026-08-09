"""ADR-009 stage 9.5: the content-addressed binary asset store.

WHAT THIS REPLACES. Image and chart bytes used to be base64'd inline into
the chat's `data` blob (see session_save.py's own _serialize_image_node).
That has two costs the ADR names: autosave rewrites every one of those
megabytes on every 30-second tick even when no image changed, and an
export has no way to carry assets as real files. Both dissolve once bytes
live outside the blob and the blob carries only a reference.

CONTENT-ADDRESSED, so the reference IS the integrity check: a ref is the
SHA-256 of the bytes it names. Two nodes holding the same image
deduplicate for free, a re-save of unchanged bytes is a no-op rather than
a rewrite, and a corrupted file is detectable by rehashing rather than
being silently served as if it were fine.

WRITES ARE ATOMIC AND IDEMPOTENT. put() writes to a temp name and
os.replace()s into place, so a crash mid-write can only ever leave a temp
file, never a truncated file wearing a real ref's name that a later read
would trust. If the target already exists, put() returns immediately
without rewriting - by construction the existing file already has exactly
the content being stored, since its name is that content's hash.

NOTHING IS EVER DELETED HERE. Garbage collection of unreferenced assets is
deliberately out of scope: an asset is cheap to keep and catastrophic to
delete while some chat still points at it, and "which chats reference
which assets" is a whole-database question this module has no business
answering. A future sweep belongs alongside the export/import story, with
its own recon.

Two-character shard directories keep any single directory from
accumulating tens of thousands of entries, which some filesystems handle
poorly - the same convention git's own object store uses.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Kept deliberately small: this maps the mime types this app actually
# produces (image nodes and chart PNGs), not a general registry. An
# unknown type falls back to .bin rather than guessing.
_EXTENSION_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}


def content_ref(data: bytes) -> str:
    """The SHA-256 hex digest that names these exact bytes."""
    return hashlib.sha256(data).hexdigest()


def extension_for_mime(mime_type: str | None) -> str:
    return _EXTENSION_BY_MIME.get((mime_type or "").lower(), "bin")


def assets_dir_for(db_path: Path) -> Path:
    """Assets live NEXT TO the database that references them
    (`db_path.parent / "assets"`), matching backend/db_backup.py's own
    backups_dir_for convention - so a test passing an isolated
    `tmp_path / "chats.db"` gets an isolated asset store for free, and the
    real default lands at ~/.graphlink/assets/."""
    return db_path.parent / "assets"


class AssetStore:
    """A directory of content-addressed blobs. Construct with the directory
    itself, not a db path, so it is equally usable for the live store and
    for an export's staging area."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path_for(self, ref: str) -> Path:
        return self.root / ref[:2] / ref

    def exists(self, ref: str) -> bool:
        return self._path_for(ref).is_file()

    def put(self, data: bytes) -> str:
        """Stores `data` and returns its ref. Idempotent: storing identical
        bytes twice writes once and returns the same ref both times."""
        ref = content_ref(data)
        target = self._path_for(ref)
        if target.is_file():
            return ref

        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        try:
            fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except BaseException:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        try:
            os.chmod(target, 0o600)
        except OSError:
            logger.warning("could not chmod asset %s to 0600 - continuing", target)
        return ref

    def get(self, ref: str) -> bytes | None:
        """The bytes for `ref`, or None if this store has never seen it.

        None rather than an exception because a missing asset must degrade
        to "this image does not render" rather than "this chat will not
        load" - a chat's text is worth far more than one of its pictures,
        and an import from a bundle whose assets were stripped is a
        legitimate, survivable state."""
        target = self._path_for(ref)
        if not target.is_file():
            return None
        try:
            return target.read_bytes()
        except OSError:
            logger.warning("could not read asset %s - treating as missing", target)
            return None

    def verify(self, ref: str) -> bool:
        """True only if the stored bytes still hash to their own name -
        the integrity check content addressing makes possible. Used by the
        import path, which is reading files it did not write."""
        data = self.get(ref)
        return data is not None and content_ref(data) == ref
