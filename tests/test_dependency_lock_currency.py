"""ADR-015 stage 15.7: requirements.txt must not silently drift from
requirements.in - a lock that no longer describes its own source is worse
than no lock, because CI keeps installing something nobody declared.

DELIBERATELY a pure text comparison, NOT a `pip-compile` shell-out. The
first version of this test did shell out, and CI proved that approach
unusable within one run: pip-tools imports a PRIVATE pip internal
(`pip._internal.utils.compat.stdlib_pkgs`) that newer pip removed, so
`pip-compile` died with an ImportError on the runner while every other
test passed. Any check that depends on pip-tools-vs-pip version skew is a
check that will keep breaking on someone else's schedule, so this
compares the two files directly instead - no subprocess, no network, no
third-party import, nothing to skew.

What this DOES catch (the dominant real decay mode): a package added to
or removed from requirements.in without regenerating requirements.txt.
pip-compile annotates every directly-requested package with a
`# via -r requirements.in` comment, so the set of those annotations in
the lock must equal the set of requirement names in the source.

What this does NOT catch, stated honestly rather than implied: a changed
VERSION CONSTRAINT on an already-present package (`openai>=1.0.0` ->
`openai>=2.0.0`) whose recompile was skipped, and transitive-dependency
drift. Catching those genuinely does require running the resolver. The
`build-check` job already installs the locked set from scratch, which is
the real backstop for "the lock doesn't actually resolve"; this test is
the cheap, deterministic guard for the far more common "someone edited
the .in and forgot to recompile."
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# "openai>=1.0.0" -> "openai"; 'tomli; python_version < "3.11"' -> "tomli".
_REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9._-]+)")

# pip-compile's own annotation for a package requirements.in asked for
# directly, in both its one-line ("# via -r requirements.in") and
# multi-line ("# via\n#   -r requirements.in\n#   other-pkg") forms.
_DIRECT_ANNOTATION = "-r requirements.in"


def _normalize(name: str) -> str:
    """PEP 503 name normalization - requirements.in says `Pillow` and
    `python-docx`, the lock says `pillow` and `python_docx`-adjacent
    spellings depending on the package; comparing raw strings would
    report drift that isn't real."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared_in_requirements_in() -> set[str]:
    names = set()
    for raw_line in (REPO_ROOT / "requirements.in").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _REQUIREMENT_NAME_RE.match(line)
        if match:
            names.add(_normalize(match.group(1)))
    return names


def _direct_packages_in_lock() -> set[str]:
    """Every package in requirements.txt whose `# via` block names
    requirements.in as a direct source. Walks the file linearly because a
    package's annotation block follows its own pinned-version line."""
    names: set[str] = set()
    current: str | None = None
    for raw_line in (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped.startswith("#") and "==" in stripped:
            current = _normalize(stripped.split("==", 1)[0])
        elif current and stripped.startswith("#") and _DIRECT_ANNOTATION in stripped:
            names.add(current)
    return names


def test_requirements_txt_covers_exactly_requirements_in():
    declared = _declared_in_requirements_in()
    locked = _direct_packages_in_lock()

    missing = sorted(declared - locked)
    extra = sorted(locked - declared)
    assert not missing and not extra, (
        "requirements.txt is stale relative to requirements.in.\n"
        f"  in requirements.in but not locked: {missing}\n"
        f"  locked as direct but no longer in requirements.in: {extra}\n"
        "Regenerate with (using this repo's pinned Python 3.12):\n"
        "  pip-compile --generate-hashes --output-file=requirements.txt requirements.in"
    )


def test_every_locked_package_is_hash_pinned():
    """The lock's whole security value is --generate-hashes: an unhashed
    entry silently opts that one package out of pip's hash verification.
    Cheap to check here, and it can only regress by someone regenerating
    the lock WITHOUT --generate-hashes."""
    lines = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    unhashed = [
        line.split("==", 1)[0].strip()
        for i, line in enumerate(lines)
        if "==" in line and not line.strip().startswith("#")
        # A hash-pinned entry always continues onto a --hash line, either
        # via a trailing backslash or on the immediately following line.
        and not (line.rstrip().endswith("\\") or (i + 1 < len(lines) and "--hash" in lines[i + 1]))
    ]
    assert not unhashed, (
        f"requirements.txt entries with no --hash pin: {unhashed}. "
        "Regenerate the lock WITH --generate-hashes."
    )
