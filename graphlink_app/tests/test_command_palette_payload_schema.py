"""The command-palette wire contract: staleness of the generated artifacts,
and - more importantly - that the contract actually describes the REAL
payload. See test_composer_payload_schema.py's module docstring for why both
matter; the shared codegen CLI mechanism is already exhaustively covered
there and not duplicated here.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from graphlink_command_palette import CommandManager
from graphlink_command_palette_bridge import CommandPaletteBridge
from graphlink_command_palette_payload import CommandPaletteStatePayload
from graphlink_island_codegen import schema_json_for, typescript_for
from graphlink_island_schema import validate_payload

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATED = _REPO_ROOT / "web_ui" / "src" / "lib" / "bridge-core" / "generated"
_SCHEMA_FILE = _GENERATED / "command-palette-state.schema.json"
_TS_FILE = _GENERATED / "command-palette-state.ts"

_TS_SOURCE_LABEL = (
    "graphlink_app/graphlink_command_palette_payload.py::CommandPaletteStatePayload"
)

_REGENERATE_HINT = (
    "Regenerate with graphlink_island_codegen.py's schema_json_for()/"
    "typescript_for() against CommandPaletteStatePayload and commit the result."
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _snapshot(*, commands=(), execute_id=None) -> dict:
    """commands: iterable of (name, aliases, condition) - condition may be
    None for always-available."""
    manager = CommandManager()
    for name, aliases, condition in commands:
        manager.register_command(name, aliases, lambda: None, condition)
    bridge = CommandPaletteBridge(manager)
    states: list[str] = []
    bridge.stateChanged.connect(states.append)
    bridge.open()
    if execute_id is not None:
        bridge.executeCommand(execute_id)
    return json.loads(states[-1])


class TestTheContractDescribesTheRealPayload:
    def test_the_initial_empty_snapshot_validates(self):
        errors = validate_payload(_snapshot(), CommandPaletteStatePayload)

        assert errors == [], errors

    def test_a_snapshot_with_real_commands_validates(self):
        errors = validate_payload(
            _snapshot(
                commands=[
                    ("New Chat", ["start new"], None),
                    ("Reset View", ["reset zoom"], None),
                ]
            ),
            CommandPaletteStatePayload,
        )

        assert errors == [], errors

    def test_a_snapshot_carrying_a_real_notice_string_validates(self):
        errors = validate_payload(
            _snapshot(commands=[("Cmd", [], lambda: False)], execute_id="0"),
            CommandPaletteStatePayload,
        )

        assert errors == [], errors


class TestValidatorActuallyRejects:
    @pytest.mark.parametrize(
        "mutate,expected_fragment",
        [
            (lambda p: p.pop("commands"), "missing required field"),
            (lambda p: p.__setitem__("visible", "yes"), "expected boolean"),
            (lambda p: p["commands"].append({"name": "x"}), "missing required field"),
            (lambda p: p.__setitem__("surprise", "x"), "unexpected field"),
        ],
    )
    def test_mutation_is_caught(self, mutate, expected_fragment):
        payload = _snapshot(commands=[("New Chat", [], None)])
        mutate(payload)

        errors = validate_payload(payload, CommandPaletteStatePayload)

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
        fresh = schema_json_for(CommandPaletteStatePayload, title="CommandPaletteState")

        assert _read(_SCHEMA_FILE) == fresh, f"{_SCHEMA_FILE.name} is stale. {_REGENERATE_HINT}"

    def test_typescript_matches_regenerating_it_now(self):
        fresh = typescript_for(CommandPaletteStatePayload, source=_TS_SOURCE_LABEL)

        assert _read(_TS_FILE) == fresh, f"{_TS_FILE.name} is stale. {_REGENERATE_HINT}"
