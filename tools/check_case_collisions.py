"""Reject tracked source paths that collide on a case-insensitive filesystem.

Read Git's *index*, not the working tree: a Windows checkout can physically
contain only one of two colliding files, obscuring the issue from filesystem
walks. The test runs in both Linux npm checks and Windows production builds.
"""

from __future__ import annotations

import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def case_collisions(paths: Iterable[str]) -> list[tuple[str, ...]]:
    """Group names that become the same checkout path after case folding."""
    names: dict[str, set[str]] = defaultdict(set)
    for name in paths:
        names[name.replace("\\", "/").casefold()].add(name)
    return sorted(
        (tuple(sorted(group)) for group in names.values() if len(group) > 1),
    )


def tracked_paths() -> list[str]:
    """List the paths Git expects to check out, even if NTFS hid one file."""
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout
    return [part.decode("utf-8", errors="surrogateescape")
            for part in listing.split(b"\0") if part]


def main() -> int:
    paths = tracked_paths()
    conflicts = case_collisions(paths)
    if conflicts:
        print("Git-tracked paths collide on case-insensitive filesystems:", file=sys.stderr)
        for conflict in conflicts:
            print("  " + "  <->  ".join(conflict), file=sys.stderr)
        return 1
    print(f"Checked {len(paths)} Git-tracked paths for case-insensitive collisions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
