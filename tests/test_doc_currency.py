"""ADR-015 stage 15.7: doc-currency checks - catches two specific, cheap-to-
detect decay modes automatically rather than relying on someone noticing.

This is deliberately narrow. GRAPHLINK_REPO_NAVIGATION.md's PROSE (is the
described architecture still accurate?) is not mechanically checkable and is
tracked as its own follow-up, not attempted here - see the doc's own
docstring note. What IS mechanically checkable, and checked below: every
concrete file/directory path the doc cites still exists on disk (the most
common decay mode - a referenced file gets renamed or deleted and the doc
silently goes stale), and the one duplicated version string
(update_signal.md) hasn't drifted from the real source of truth
(graphlink_version.APP_VERSION).
"""

from __future__ import annotations

import re
from pathlib import Path

import graphlink_version

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_update_signal_matches_app_version():
    update_signal = (REPO_ROOT / "update_signal.md").read_text(encoding="utf-8").strip()
    assert update_signal == graphlink_version.APP_VERSION, (
        f"update_signal.md says {update_signal!r} but graphlink_version.APP_VERSION "
        f"is {graphlink_version.APP_VERSION!r} - these are meant to track the same "
        "release; update whichever one fell behind."
    )


# Deliberately scoped to the "Concrete File Index" section only, not the
# whole document: the rest of the doc is free-form prose that legitimately
# names files that no longer exist (narrating what got deleted at the
# R7.6b cutover, for instance) - a plain "does this string resolve to a
# path" scan false-positives heavily on that. The File Index section is
# different in kind: its own text calls it "the practical lookup map for
# where code actually lives today", and it follows one consistent,
# parseable convention throughout - a `### \`<dir>/\` (...)` sub-heading
# establishing a directory, then `- \`<filename>\` - ...` bullets naming
# real files inside it (bare, relying on that heading's context, exactly
# the convention this check has to track rather than assume away).
_SECTION_RE = re.compile(r"^## Concrete File Index\n(.*?)(?=\n## |\Z)", re.DOTALL | re.MULTILINE)
# Matches every "### " sub-heading, backtick-quoted directory or not (e.g.
# "### Loose top-level modules that matter most" has no directory at all -
# those files live at the repo root, group(1) is None) - so a heading with
# no directory correctly RESETS current_dir to "" instead of silently
# inheriting the previous subsection's.
_SUBHEADING_RE = re.compile(r"^### (?:`([^`]+)`)?", re.MULTILINE)
_BULLET_RE = re.compile(r"^- `([^`]+)`", re.MULTILINE)


def test_repo_navigation_doc_file_index_paths_exist():
    doc_path = REPO_ROOT / "GRAPHLINK_REPO_NAVIGATION.md"
    text = doc_path.read_text(encoding="utf-8")

    section_match = _SECTION_RE.search(text)
    assert section_match, "GRAPHLINK_REPO_NAVIGATION.md's '## Concrete File Index' section is missing"
    section = section_match.group(1)

    # Interleave sub-heading and bullet matches in document order so each
    # bullet resolves against whichever `### \`dir/\`` heading precedes it.
    events = sorted(
        [(m.start(), "dir", m.group(1)) for m in _SUBHEADING_RE.finditer(section)]
        + [(m.start(), "file", m.group(1)) for m in _BULLET_RE.finditer(section)]
    )

    missing: list[str] = []
    current_dir = ""
    for _, kind, value in events:
        if kind == "dir":
            current_dir = value or ""
            continue
        candidate = value.split("::", 1)[0]  # "app.py::create_app" -> "app.py"
        if "*" in candidate:
            if not list((REPO_ROOT / current_dir).glob(candidate)):
                missing.append(f"{current_dir}{candidate}")
            continue
        if not (REPO_ROOT / current_dir / candidate).exists():
            missing.append(f"{current_dir}{candidate}")

    assert not missing, (
        "GRAPHLINK_REPO_NAVIGATION.md's Concrete File Index references path(s) "
        f"that no longer exist: {missing}. Either the file was renamed/deleted "
        "(update the doc) or it moved to a different subsection's directory "
        "(fix which `### \\`dir/\\`` heading it's listed under)."
    )
