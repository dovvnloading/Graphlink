"""The composer wire contract: staleness of the generated artifacts, and -
more importantly - that the contract actually describes the REAL payload.

Two independent guarantees, and the second is the one that matters:

1. STALENESS: the checked-in schema/TS files match what regenerating them now
   would produce (the pattern already used for gl-theme.css and
   gl-vars-dev.css).
2. TRUTHFULNESS: a real ComposerBridge snapshot - built by the actual
   _build_state_payload() code path, across every route mode and request state
   - validates against the payload dataclasses.

(1) alone would be near-worthless: it only proves the generator is
self-consistent. The dataclasses could describe a payload the bridge has never
emitted and every staleness test would still pass. (2) is what makes
graphlink_composer_payload.py authoritative rather than aspirational, and it is
why these live in one file - regenerating the artifacts without re-proving they
still match reality is exactly the drift this whole pipeline exists to stop.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import graphlink_config as config
from graphlink_composer import ComposerController, ComposerRequestState
from graphlink_composer_bridge import ComposerBridge
from graphlink_composer_payload import ComposerStatePayload
from graphlink_island_codegen import schema_json_for, typescript_for
from graphlink_island_schema import (
    SchemaGenerationError,
    json_schema_for,
    validate_payload,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATED = _REPO_ROOT / "web_ui" / "src" / "lib" / "bridge-core" / "generated"
_SCHEMA_FILE = _GENERATED / "composer-state.schema.json"
_TS_FILE = _GENERATED / "composer-state.ts"

_TS_SOURCE_LABEL = "graphlink_app/graphlink_composer_payload.py::ComposerStatePayload"

_REGENERATE_HINT = (
    "Regenerate with graphlink_island_codegen.py's schema_json_for()/"
    "typescript_for() against ComposerStatePayload and commit the result."
)


def _read(path: Path) -> str:
    # Universal-newline mode, never newline="" - .gitattributes sets
    # `* text=auto`, so a fresh checkout can materialize these with CRLF.
    return path.read_text(encoding="utf-8")


class _Settings:
    def __init__(self, mode: str) -> None:
        self._mode = mode

    def get_current_mode(self):
        return self._mode

    def get_ollama_chat_model(self):
        return "qwen2.5:7b"

    def get_ollama_scanned_models(self):
        return ["qwen2.5:7b", "llama3:8b"]

    def get_llama_cpp_chat_model_path(self):
        return "C:/models/foo.gguf"

    def get_llama_cpp_scanned_models(self):
        return ["C:/models/foo.gguf"]

    def get_api_models(self, provider=None):
        return {config.TASK_CHAT: "gpt-4o"}

    def get_api_model_catalog(self, provider=None):
        return [
            {"model_id": "gpt-4o", "ready": True, "available": True, "capabilities": ["vision"]}
        ]

    def get_ollama_reasoning_mode(self):
        return "Thinking"

    def get_llama_cpp_reasoning_mode(self):
        return "Quick"


class _Node:
    title = "Chart analysis"
    persistent_id = "node-7"


class _Window:
    def __init__(self, mode: str, *, rich: bool) -> None:
        self.settings_manager = _Settings(mode)
        self.current_node = _Node() if rich else None
        self.pending_attachments = (
            [
                {
                    "attachment_id": "a1",
                    "path": "C:/private/x.csv",
                    "name": "x.csv",
                    "kind": "document",
                    "token_count": 42,
                    "preparation_state": "ready",
                    "context_label": "Data",
                }
            ]
            if rich
            else []
        )
        self.composer_controller = None


def _snapshot(
    mode: str,
    *,
    rich: bool,
    draft_text: str = "",
    request_state: ComposerRequestState | None = None,
) -> dict:
    controller = ComposerController()
    if request_state is not None:
        # The real public transition API (ComposerController.set_state), not
        # a raw attribute assignment - a real state machine may reject an
        # invalid transition, and this snapshot should reflect what the
        # bridge actually publishes for a state a caller can really reach.
        controller.set_state(request_state)
    bridge = ComposerBridge(_Window(mode, rich=rich), controller)
    states: list[str] = []
    bridge.stateChanged.connect(states.append)
    if draft_text:
        bridge.updateDraft(draft_text)
    else:
        bridge.ready()
    return json.loads(states[-1])


_ALL_MODES = ["Ollama (Local)", config.MODE_API_ENDPOINT, config.MODE_LLAMACPP_LOCAL]
_ALL_REQUEST_STATES = list(ComposerRequestState)


class TestTheContractDescribesTheRealPayload:
    """The load-bearing tests: reality must match the dataclasses."""

    @pytest.mark.parametrize("mode", _ALL_MODES)
    @pytest.mark.parametrize("rich", [False, True])
    def test_a_real_bridge_snapshot_validates(self, mode, rich):
        errors = validate_payload(_snapshot(mode, rich=rich), ComposerStatePayload)

        assert errors == [], (
            f"A real ComposerBridge payload (mode={mode!r}, rich={rich}) does not match "
            f"ComposerStatePayload. Either the bridge changed shape or the contract is "
            f"wrong - fix whichever actually drifted:\n  " + "\n  ".join(errors)
        )

    @pytest.mark.parametrize("mode", _ALL_MODES)
    def test_a_snapshot_with_a_draft_validates(self, mode):
        errors = validate_payload(_snapshot(mode, rich=True, draft_text="hello"), ComposerStatePayload)

        assert errors == []

    @pytest.mark.parametrize("mode", _ALL_MODES)
    @pytest.mark.parametrize("rich", [False, True])
    @pytest.mark.parametrize("request_state", _ALL_REQUEST_STATES)
    def test_every_route_mode_x_richness_x_request_state_combination_validates(
        self, mode, rich, request_state
    ):
        # Closes a real gap adversarial review found: this exact
        # 3 modes x 2 richness x 9 request states matrix was verified once by
        # hand in an ad hoc shell one-liner during development and never
        # turned into a committed test - so the claim in this increment's own
        # PR description ("validated across 3 route modes x rich/empty x 6
        # request states") corresponded to nothing a future change could
        # break loudly. It is 9 request states, not 6 - ComposerRequestState
        # has 9 members; the original manual check undercounted it.
        errors = validate_payload(
            _snapshot(mode, rich=rich, request_state=request_state), ComposerStatePayload
        )

        assert errors == [], (
            f"mode={mode!r} rich={rich} request_state={request_state!r}: {errors}"
        )

    def test_the_id_not_path_firewall_still_holds_in_the_contract(self):
        # ComposerAttachmentPayload deliberately has no `path` field. If someone
        # adds one to make the contract "match" a bridge that started leaking
        # paths, this fails - the contract is not allowed to legitimize that.
        snapshot = _snapshot("Ollama (Local)", rich=True)

        assert "C:/private/x.csv" not in json.dumps(snapshot)
        for item in snapshot["context"]["items"]:
            assert "path" not in item


class TestValidatorActuallyRejects:
    """Guards the guard: a validator that accepts everything proves nothing."""

    @pytest.mark.parametrize(
        "mutate,expected_fragment",
        [
            (lambda p: p["draft"].pop("text"), "missing required field"),
            (lambda p: p["draft"].__setitem__("text", 5), "expected string"),
            (lambda p: p["context"].__setitem__("totalTokens", True), "expected integer"),
            (lambda p: p["draft"].__setitem__("restored", 1), "expected boolean"),
            (lambda p: p["request"].__setitem__("state", "bogus"), "is not one of"),
            (lambda p: p["route"].__setitem__("mode", "quantum"), "is not one of"),
            (lambda p: p["draft"].__setitem__("surprise", "x"), "unexpected field"),
            (lambda p: p["draft"].__setitem__("text", None), "null is not allowed"),
            (lambda p: p.__setitem__("theme", []), "expected object"),
            (lambda p: p["theme"]["cssVariables"].__setitem__("--gl-x", 7), "expected string"),
            (lambda p: p["route"]["reasoning"]["options"].append({"id": "x"}), "missing required field"),
        ],
    )
    def test_mutation_is_caught(self, mutate, expected_fragment):
        payload = _snapshot("Ollama (Local)", rich=True)
        mutate(payload)

        errors = validate_payload(payload, ComposerStatePayload)

        assert errors, "mutation was not caught - the validator is too permissive"
        assert any(expected_fragment in error for error in errors), (
            f"expected an error containing {expected_fragment!r}, got {errors}"
        )


class TestGeneratedArtifactsAreNotStale:
    def test_schema_file_exists(self):
        assert _SCHEMA_FILE.is_file(), f"{_SCHEMA_FILE} is missing. {_REGENERATE_HINT}"

    def test_ts_file_exists(self):
        assert _TS_FILE.is_file(), f"{_TS_FILE} is missing. {_REGENERATE_HINT}"

    def test_schema_matches_regenerating_it_now(self):
        fresh = schema_json_for(ComposerStatePayload, title="ComposerState")

        assert _read(_SCHEMA_FILE) == fresh, f"{_SCHEMA_FILE.name} is stale. {_REGENERATE_HINT}"

    def test_typescript_matches_regenerating_it_now(self):
        fresh = typescript_for(ComposerStatePayload, source=_TS_SOURCE_LABEL)

        assert _read(_TS_FILE) == fresh, f"{_TS_FILE.name} is stale. {_REGENERATE_HINT}"


class TestCheckCliClosesTheNpmRunCheckDriftGap:
    """`python graphlink_island_codegen.py --check` is what `npm run check`
    shells out to (via the `check:schema` script) - the mechanism that makes
    section 3.3's "npm run check fails on drift" clause literally true, not
    just true of the separate Python pytest suite. Exercised as a real
    subprocess, not by calling _main() in-process, so this proves the exact
    command npm invokes actually behaves as claimed."""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(_REPO_ROOT / "graphlink_app" / "graphlink_island_codegen.py"), *args],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
        )

    @staticmethod
    def _restore(path: Path, original_text: str, stat_before: os.stat_result) -> None:
        """Restore a generated file's content AND its mtime. The restore
        write alone bumps mtime, and graphlink_frontend_bootstrap's island
        staleness check is mtime-based - a suite run that leaves generated
        files looking newer than the built assets makes the next real app
        launch rebuild islands for no reason (the 19-island npm storm that
        presented as the app looping at startup without ever showing a
        window)."""
        path.write_text(original_text, encoding="utf-8", newline="\n")
        os.utime(path, ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))

    def test_check_passes_and_exits_zero_when_artifacts_are_current(self):
        result = self._run("--check")

        assert result.returncode == 0, result.stderr
        assert "up to date" in result.stdout

    def test_check_fails_and_exits_nonzero_when_a_generated_file_is_hand_edited(self):
        original = _read(_TS_FILE)
        stat_before = _TS_FILE.stat()
        try:
            _TS_FILE.write_text(original + "\n// hand-edited\n", encoding="utf-8", newline="\n")
            result = self._run("--check")

            assert result.returncode == 1
            assert "stale" in result.stderr.lower()
            assert str(_TS_FILE.name) in result.stderr
        finally:
            self._restore(_TS_FILE, original, stat_before)

    def test_check_fails_when_a_generated_file_is_missing(self):
        original = _read(_SCHEMA_FILE)
        stat_before = _SCHEMA_FILE.stat()
        try:
            _SCHEMA_FILE.unlink()
            result = self._run("--check")

            assert result.returncode == 1
            assert "missing" in result.stderr.lower()
        finally:
            self._restore(_SCHEMA_FILE, original, stat_before)

    def test_write_regenerates_a_hand_edited_file_back_to_the_real_contract(self):
        original = _read(_TS_FILE)
        stat_before = _TS_FILE.stat()
        try:
            _TS_FILE.write_text("// corrupted\n", encoding="utf-8", newline="\n")

            result = self._run("--write")

            assert result.returncode == 0, result.stderr
            assert _read(_TS_FILE) == original
        finally:
            self._restore(_TS_FILE, original, stat_before)

    def test_write_leaves_up_to_date_files_untouched(self):
        # The root cause of the rebuild storm: --write used to rewrite every
        # generated file for every island unconditionally, bumping ~38 mtimes
        # per run even when the content was byte-identical. It must now leave
        # an already-current file's mtime alone.
        mtime_before = _TS_FILE.stat().st_mtime_ns
        schema_mtime_before = _SCHEMA_FILE.stat().st_mtime_ns

        result = self._run("--write")

        assert result.returncode == 0, result.stderr
        assert _TS_FILE.stat().st_mtime_ns == mtime_before
        assert _SCHEMA_FILE.stat().st_mtime_ns == schema_mtime_before

    def test_schema_is_valid_json_and_declares_its_draft(self):
        schema = json.loads(_read(_SCHEMA_FILE))

        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["title"] == "ComposerState"
        assert schema["type"] == "object"


class TestSchemaGeneratorRefusesToGuess:
    """The generator must fail loudly on types it cannot represent, rather
    than emitting a schema that quietly disagrees with the real payload."""

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
        from typing import Literal

        @dataclass
        class HasIntLiteral:
            value: Literal[1, 2]

        with pytest.raises(SchemaGenerationError, match="string Literal"):
            json_schema_for(HasIntLiteral)

    def test_non_dataclass_raises(self):
        with pytest.raises(SchemaGenerationError, match="not a dataclass"):
            json_schema_for(dict)
