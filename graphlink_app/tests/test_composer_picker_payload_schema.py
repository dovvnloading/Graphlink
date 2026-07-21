"""The composer-picker wire contract: staleness of the generated artifacts,
and that the contract actually describes the REAL payload (including its
nested ComposerPickerOption list).

See test_composer_payload_schema.py's module docstring for why both matter;
the shared codegen mechanism itself is exhaustively covered there and not
duplicated here.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from graphlink_composer_picker_bridge import ComposerPickerBridge
from graphlink_composer_picker_payload import ComposerPickerStatePayload
from graphlink_island_codegen import schema_json_for, typescript_for
from graphlink_island_schema import validate_payload

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATED = _REPO_ROOT / "web_ui" / "src" / "lib" / "bridge-core" / "generated"
_SCHEMA_FILE = _GENERATED / "composer-picker-state.schema.json"
_TS_FILE = _GENERATED / "composer-picker-state.ts"

_TS_SOURCE_LABEL = "graphlink_app/graphlink_composer_picker_payload.py::ComposerPickerStatePayload"

_REGENERATE_HINT = (
    "Regenerate with graphlink_island_codegen.py's schema_json_for()/"
    "typescript_for() against ComposerPickerStatePayload and commit the result."
)


class _FakeComposerBridge:
    def __init__(self):
        self.window = None

    def route_snapshot(self):
        return {
            "provider": "Ollama",
            "modelId": "llama3",
            "modelOptions": [
                {"id": "llama3", "label": "Llama 3", "active": True, "ready": True, "available": True, "source": "installed"},
            ],
            "reasoning": {"level": "Thinking", "options": [{"id": "Quick", "label": "Quick", "description": "Fast."}]},
        }

    def selectModel(self, model_id):
        pass

    def setReasoningLevel(self, level):
        pass


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _snapshot(kind: str = "model") -> dict:
    bridge = ComposerPickerBridge(_FakeComposerBridge())
    bridge.open(kind)
    states: list[str] = []
    bridge.stateChanged.connect(states.append)
    bridge.publish()
    return json.loads(states[-1])


class TestTheContractDescribesTheRealPayload:
    def test_a_real_bridge_snapshot_validates(self):
        errors = validate_payload(_snapshot(), ComposerPickerStatePayload)

        assert errors == [], errors

    def test_the_nested_options_carry_the_expected_fields(self):
        option = _snapshot()["options"][0]

        assert set(option.keys()) == {"id", "label", "meta", "current", "unavailable"}

    def test_a_reasoning_snapshot_also_validates(self):
        errors = validate_payload(_snapshot("reasoning"), ComposerPickerStatePayload)

        assert errors == [], errors


class TestValidatorActuallyRejects:
    @pytest.mark.parametrize(
        "mutate,expected_fragment",
        [
            (lambda p: p.pop("schemaVersion"), "missing required field"),
            (lambda p: p.pop("kind"), "missing required field"),
            (lambda p: p.pop("options"), "missing required field"),
            (lambda p: p.__setitem__("options", "not a list"), "expected array"),
            (lambda p: p["options"][0].pop("current"), "missing required field"),
            (lambda p: p.__setitem__("surprise", "x"), "unexpected field"),
        ],
    )
    def test_mutation_is_caught(self, mutate, expected_fragment):
        payload = _snapshot()
        mutate(payload)

        errors = validate_payload(payload, ComposerPickerStatePayload)

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
        fresh = schema_json_for(ComposerPickerStatePayload, title="ComposerPickerState")

        assert _read(_SCHEMA_FILE) == fresh, f"{_SCHEMA_FILE.name} is stale. {_REGENERATE_HINT}"

    def test_typescript_matches_regenerating_it_now(self):
        fresh = typescript_for(ComposerPickerStatePayload, source=_TS_SOURCE_LABEL)

        assert _read(_TS_FILE) == fresh, f"{_TS_FILE.name} is stale. {_REGENERATE_HINT}"
