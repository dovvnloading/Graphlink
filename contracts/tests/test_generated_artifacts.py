"""Contract coverage for contracts/codegen.py and its generated artifacts.

Qt-removal plan R7.6b. This REPLACES the per-payload schema tests that lived in
graphlink_app/tests/ (test_<island>_payload_schema.py, one file per payload).
Those were deleted with graphlink_app/, and porting them one-for-one was not
possible: every one of them imported a Qt bridge class (DragSpeedBridge,
NotificationBridge, ...) to exercise the payload alongside its Qt publisher,
and those bridges no longer exist.

Coverage is BROADER here, not narrower. The old files covered 5 of the payloads
that survived the cutover; this covers all 11, by parametrizing over
GENERATED_ARTIFACTS itself rather than naming payloads one at a time. A new
codegen entry is picked up automatically instead of shipping unguarded until
someone remembers to write its file.

What is genuinely gone with the bridges: the "bridge publishes a payload the
schema accepts" round-trip. The Qt publishers it tested no longer exist, and
their SPA successors are covered by backend/tests + the vitest suite against
the generated TypeScript.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codegen import GENERATED_ARTIFACTS, schema_json_for, typescript_for  # noqa: E402
from payload_schema import SchemaGenerationError, json_schema_for  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CODEGEN = _REPO_ROOT / "contracts" / "codegen.py"


def _read(path: Path) -> str:
    # Universal-newline mode, never newline="" - .gitattributes sets
    # `* text=auto`, so a fresh checkout can materialize these with CRLF.
    return path.read_text(encoding="utf-8")


def _resolve(entry: dict) -> type:
    module_name, class_name = entry["dataclass_import"]
    module = __import__(module_name)
    return getattr(module, class_name)


def _ids(entry: dict) -> str:
    return entry["title"]


ALL = pytest.mark.parametrize("entry", GENERATED_ARTIFACTS, ids=_ids)


class TestEveryArtifactIsPresentAndCurrent:
    """The staleness guard, applied to every entry in the registry."""

    @ALL
    def test_schema_file_exists(self, entry):
        assert entry["schema_path"].is_file(), (
            f"{entry['schema_path']} is missing. Regenerate: python contracts/codegen.py --write"
        )

    @ALL
    def test_ts_file_exists(self, entry):
        assert entry["ts_path"].is_file(), (
            f"{entry['ts_path']} is missing. Regenerate: python contracts/codegen.py --write"
        )

    @ALL
    def test_schema_matches_regenerating_it_now(self, entry):
        fresh = schema_json_for(_resolve(entry), title=entry["title"])
        assert _read(entry["schema_path"]) == fresh, (
            f"{entry['schema_path'].name} is stale. Regenerate: python contracts/codegen.py --write"
        )

    @ALL
    def test_typescript_matches_regenerating_it_now(self, entry):
        fresh = typescript_for(_resolve(entry), source=entry["source"])
        assert _read(entry["ts_path"]) == fresh, (
            f"{entry['ts_path'].name} is stale. Regenerate: python contracts/codegen.py --write"
        )

    @ALL
    def test_schema_is_valid_json_and_declares_its_draft(self, entry):
        schema = json.loads(_read(entry["schema_path"]))

        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["title"] == entry["title"]
        assert schema["type"] == "object"

    @ALL
    def test_the_payload_module_is_qt_free(self, entry):
        """The whole point of relocating these out of graphlink_app/. A payload
        that reaches for Qt would drag the dependency back into the one package
        the frontend build shells out to."""
        module_name, _ = entry["dataclass_import"]
        source = _read(_REPO_ROOT / "contracts" / f"{module_name}.py")

        assert "PySide6" not in source and "PyQt" not in source


class TestCheckCliClosesTheNpmRunCheckDriftGap:
    """`python contracts/codegen.py --check` is what `npm run check` shells out
    to (via the `check:schema` script) - the mechanism that makes "npm run check
    fails on drift" literally true, not just true of the Python pytest suite.
    Exercised as a real subprocess, not by calling _main() in-process, so this
    proves the exact command npm invokes actually behaves as claimed.

    R7.6b repointed the fixture from composer-state (an island artifact, deleted
    with the islands) to scene-state, which the SPA imports.
    """

    _ENTRY = next(e for e in GENERATED_ARTIFACTS if e["title"] == "SceneState")
    _TS_FILE = _ENTRY["ts_path"]
    _SCHEMA_FILE = _ENTRY["schema_path"]

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_CODEGEN), *args],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
        )

    @staticmethod
    def _restore(path: Path, original_text: str, stat_before: os.stat_result) -> None:
        """Restore content AND mtime. The restore write alone bumps mtime, and
        leaving generated files looking newer than build output makes the next
        `npm run build` do needless work."""
        path.write_text(original_text, encoding="utf-8", newline="\n")
        os.utime(path, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))

    def test_check_passes_and_exits_zero_when_artifacts_are_current(self):
        result = self._run("--check")

        assert result.returncode == 0, result.stderr
        assert "up to date" in result.stdout

    def test_check_fails_and_exits_nonzero_when_a_generated_file_is_hand_edited(self):
        original = _read(self._TS_FILE)
        stat_before = self._TS_FILE.stat()
        try:
            self._TS_FILE.write_text(original + "\n// hand-edited\n", encoding="utf-8", newline="\n")
            result = self._run("--check")

            assert result.returncode == 1
            assert "stale" in result.stderr.lower()
            assert self._TS_FILE.name in result.stderr
        finally:
            self._restore(self._TS_FILE, original, stat_before)

    def test_check_fails_when_a_generated_file_is_missing(self):
        original = _read(self._SCHEMA_FILE)
        stat_before = self._SCHEMA_FILE.stat()
        try:
            self._SCHEMA_FILE.unlink()
            result = self._run("--check")

            assert result.returncode == 1
            assert "missing" in result.stderr.lower()
        finally:
            self._restore(self._SCHEMA_FILE, original, stat_before)

    def test_write_regenerates_a_hand_edited_file_back_to_the_real_contract(self):
        original = _read(self._TS_FILE)
        stat_before = self._TS_FILE.stat()
        try:
            self._TS_FILE.write_text("// corrupted\n", encoding="utf-8", newline="\n")

            result = self._run("--write")

            assert result.returncode == 0, result.stderr
            assert _read(self._TS_FILE) == original
        finally:
            self._restore(self._TS_FILE, original, stat_before)

    def test_write_leaves_up_to_date_files_untouched(self):
        # --write used to rewrite every generated file unconditionally, bumping
        # mtimes even when content was byte-identical. It must leave an
        # already-current file's mtime alone.
        mtime_before = self._TS_FILE.stat().st_mtime_ns
        schema_mtime_before = self._SCHEMA_FILE.stat().st_mtime_ns

        result = self._run("--write")

        assert result.returncode == 0, result.stderr
        assert self._TS_FILE.stat().st_mtime_ns == mtime_before
        assert self._SCHEMA_FILE.stat().st_mtime_ns == schema_mtime_before


class TestSchemaGeneratorRefusesToGuess:
    """The generator must fail loudly on types it cannot represent, rather than
    emitting a schema that quietly disagrees with the real payload."""

    def test_unsupported_type_raises(self):
        from dataclasses import dataclass

        @dataclass
        class HasUnsupportedField:
            when: complex

        with pytest.raises(SchemaGenerationError, match="unsupported type"):
            json_schema_for(HasUnsupportedField)

    def test_multi_type_union_raises(self):
        from dataclasses import dataclass

        @dataclass
        class HasRealUnion:
            value: int | str

        with pytest.raises(SchemaGenerationError, match="not supported"):
            json_schema_for(HasRealUnion)

    def test_non_string_literal_raises(self):
        from dataclasses import dataclass

        @dataclass
        class HasIntLiteral:
            value: Literal[1, 2]

        with pytest.raises(SchemaGenerationError, match="string Literal"):
            json_schema_for(HasIntLiteral)

    def test_non_dataclass_raises(self):
        with pytest.raises(SchemaGenerationError, match="not a dataclass"):
            json_schema_for(dict)


def test_the_registry_covers_exactly_what_the_spa_imports():
    """R7.6a pruned this registry to the SPA's real import set and R7.6b made
    that permanent. The trap worth guarding: five entries (drag-speed,
    font-control, grid-control, notification, token-counter) look island-shaped
    but ARE imported by the SPA, so pruning by name rather than by real import
    sites would delete types still in use."""
    app_root = _REPO_ROOT / "web_ui" / "src" / "app"
    imported = set()
    for path in list(app_root.rglob("*.ts")) + list(app_root.rglob("*.tsx")):
        text = _read(path)
        for entry in GENERATED_ARTIFACTS:
            if f"generated/{entry['ts_path'].stem}" in text:
                imported.add(entry["ts_path"].stem)

    declared = {e["ts_path"].stem for e in GENERATED_ARTIFACTS}

    assert declared == imported, (
        "every codegen entry must be imported by the SPA, and vice versa; "
        f"unused={sorted(declared - imported)} missing={sorted(imported - declared)}"
    )
