"""Read-only evidence index for Review Lens unified diffs.

A model-supplied path/line is a *claim*, not an anchor. Only actual new-side
hunk lines from the fetched diff may count as visible. Parser deliberately
fails closed on malformed/combined/binary diffs and hunkless metadata; it
never looks up the filesystem and never trusts a path solely because the
model or the GitHub file listing supplied it.

A visible location is NOT proof of the model's diagnosis. We expose short,
actual source excerpts so reviewers can compare the claim to the diff.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@(?:.*)?$")
_MAX_EXCERPT = 180


@dataclass(frozen=True)
class DiffLine:
    kind: str  # "added" or "context"; not a claim about correctness
    source: str


@dataclass
class DiffEvidence:
    """Only paths and line numbers actually parsed from new-side diff hunks."""

    lines: dict[str, dict[int, DiffLine]] = field(default_factory=dict)

    def locate(self, path: str, line: int) -> DiffLine | None:
        if not isinstance(path, str) or not isinstance(line, int) or line <= 0:
            return None
        return self.lines.get(path, {}).get(line)

    def contains_path(self, path: str) -> bool:
        return isinstance(path, str) and path in self.lines


def _header_path(header: str, prefix: str) -> str | None:
    """Extract a literal Git diff filename, not a substring match.

    Git normally uses +++ b/foo; with core.quotePath it may emit a quoted
    C-like string (including escaped spaces). Unknown encodings fail closed.
    """
    value = header.strip()
    if value == "/dev/null":
        return None
    if value.startswith('"'):
        try:
            literal = ast.literal_eval(value)
        except (SyntaxError, ValueError, TypeError, UnicodeError):
            return None
        if not isinstance(literal, str):
            return None
        value = literal
    if not value.startswith(prefix):
        return None
    path = value[len(prefix):]
    if not path or "\x00" in path or "\n" in path or "\r" in path:
        return None
    return path


def index_visible_diff(diff_text: str) -> DiffEvidence:
    """Parse ordinary unified diff --git sections, including multiple hunks.

    A line is accepted only after a matching +++ file header AND an @@ hunk
    header; +/- metadata outside hunks can never turn into code evidence.
    Deleted and binary files have no new-side line anchors. Context lines
    are visible but not newly added, and are distinguished from added lines.
    """
    result = DiffEvidence()
    if not isinstance(diff_text, str):
        return result

    saw_diff_header = False
    current_path: str | None = None
    next_new_line: int | None = None
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            saw_diff_header = True
            current_path = None
            next_new_line = None
            continue
        if not saw_diff_header:
            continue
        if raw.startswith("+++ ") and next_new_line is None:
            # Inside an @@ hunk, +++ may be an added code line whose text
            # starts with "++ ". It is NOT another file header.
            current_path = _header_path(raw[4:], "b/")
            next_new_line = None
            if current_path is not None:
                result.lines.setdefault(current_path, {})
            continue
        match = _HUNK.match(raw)
        if match:
            next_new_line = int(match.group(1)) if current_path is not None else None
            continue
        if next_new_line is None or current_path is None:
            continue
        if raw.startswith("+"):
            result.lines[current_path][next_new_line] = DiffLine(
                "added", raw[1:1 + _MAX_EXCERPT]
            )
            next_new_line += 1
        elif raw.startswith(" "):
            result.lines[current_path][next_new_line] = DiffLine(
                "context", raw[1:1 + _MAX_EXCERPT]
            )
            next_new_line += 1
        elif raw.startswith("-"):
            # A removed line has no new-side number. Never attach an error
            # reported for a deleted line to an unrelated new-side line.
            continue
        elif raw.startswith("\\ No newline at end of file"):
            continue
        else:
            # Invalid hunk body: do not carry a stale counter into metadata.
            next_new_line = None
    return result


def assess_model_location(index: DiffEvidence, path: str, line: int) -> tuple[str, str]:
    """Return (status, excerpt): added, context, file or unverified.

    File-level claims are provisional even when the path exists. A concrete
    changed-line anchor is the only basis for presenting a model error as
    a source-anchored finding. The excerpt is always from the diff, never
    text supplied by the LLM.
    """
    loc = index.locate(path, line)
    if loc is not None:
        return loc.kind, loc.source
    if index.contains_path(path) and line == 0:
        return "file", ""
    return "unverified", ""
