"""The plugin-picker wire contract: staleness of the generated artifacts,
and that the contract actually describes the REAL payload (including its
nested PluginCategory/PluginEntry lists).

See test_composer_payload_schema.py's module docstring for why both matter;
the shared codegen mechanism itself is exhaustively covered there and not
duplicated here.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from graphlink_plugin_picker_bridge import PluginPickerBridge
from graphlink_plugin_picker_payload import PluginPickerStatePayload
from graphlink_island_codegen import schema_json_for, typescript_for
from graphlink_island_schema import validate_payload

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATED = _REPO_ROOT / "web_ui" / "src" / "lib" / "bridge-core" / "generated"
_SCHEMA_FILE = _GENERATED / "plugin-picker-state.schema.json"
_TS_FILE = _GENERATED / "plugin-picker-state.ts"

_TS_SOURCE_LABEL = "graphlink_app/graphlink_plugin_picker_payload.py::PluginPickerStatePayload"

_REGENERATE_HINT = (
    "Regenerate with graphlink_island_codegen.py's schema_json_for()/"
    "typescript_for() against PluginPickerStatePayload and commit the result."
)


class _FakePluginPortal:
    def get_plugin_categories(self):
        return [
            {
                "name": "Build & Execution",
                "description": "Code generation and execution tools.",
                "icon": "fa5s.code",
                "plugins": [
                    {
                        "name": "Py-Coder",
                        "description": "Opens a Python execution environment.",
                        "callback": lambda: None,
                        "category": "Build & Execution",
                        "icon": "fa5s.laptop-code",
                    },
                ],
            },
        ]

    def execute_plugin(self, plugin_name):
        pass


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _snapshot() -> dict:
    bridge = PluginPickerBridge(_FakePluginPortal())
    states: list[str] = []
    bridge.stateChanged.connect(states.append)
    bridge.publish()
    return json.loads(states[-1])


class TestTheContractDescribesTheRealPayload:
    def test_a_real_bridge_snapshot_validates(self):
        errors = validate_payload(_snapshot(), PluginPickerStatePayload)

        assert errors == [], errors

    def test_the_nested_category_carries_the_expected_fields(self):
        category = _snapshot()["categories"][0]

        assert set(category.keys()) == {"name", "description", "plugins"}

    def test_the_nested_plugin_carries_the_expected_fields(self):
        plugin = _snapshot()["categories"][0]["plugins"][0]

        assert set(plugin.keys()) == {"name", "description"}


class TestValidatorActuallyRejects:
    @pytest.mark.parametrize(
        "mutate,expected_fragment",
        [
            (lambda p: p.pop("schemaVersion"), "missing required field"),
            (lambda p: p.pop("categories"), "missing required field"),
            (lambda p: p.__setitem__("categories", "not a list"), "expected array"),
            (lambda p: p["categories"][0].pop("plugins"), "missing required field"),
            (lambda p: p["categories"][0]["plugins"][0].pop("description"), "missing required field"),
            (lambda p: p.__setitem__("surprise", "x"), "unexpected field"),
        ],
    )
    def test_mutation_is_caught(self, mutate, expected_fragment):
        payload = _snapshot()
        mutate(payload)

        errors = validate_payload(payload, PluginPickerStatePayload)

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
        fresh = schema_json_for(PluginPickerStatePayload, title="PluginPickerState")

        assert _read(_SCHEMA_FILE) == fresh, f"{_SCHEMA_FILE.name} is stale. {_REGENERATE_HINT}"

    def test_typescript_matches_regenerating_it_now(self):
        fresh = typescript_for(PluginPickerStatePayload, source=_TS_SOURCE_LABEL)

        assert _read(_TS_FILE) == fresh, f"{_TS_FILE.name} is stale. {_REGENERATE_HINT}"
