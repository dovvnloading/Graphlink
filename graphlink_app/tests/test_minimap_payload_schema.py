"""The minimap wire contract: staleness of the generated artifacts, and
that the contract actually describes the REAL payload (including its
nested MinimapNodeEntry list).

See test_composer_payload_schema.py's module docstring for why both matter;
the shared codegen mechanism itself is exhaustively covered there and not
duplicated here.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PySide6.QtCore import QObject, Signal

from graphlink_minimap_bridge import MinimapBridge
from graphlink_minimap_payload import MinimapStatePayload
from graphlink_island_codegen import schema_json_for, typescript_for
from graphlink_island_schema import validate_payload

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATED = _REPO_ROOT / "web_ui" / "src" / "lib" / "bridge-core" / "generated"
_SCHEMA_FILE = _GENERATED / "minimap-state.schema.json"
_TS_FILE = _GENERATED / "minimap-state.ts"

_TS_SOURCE_LABEL = "graphlink_app/graphlink_minimap_payload.py::MinimapStatePayload"

_REGENERATE_HINT = (
    "Regenerate with graphlink_island_codegen.py's schema_json_for()/"
    "typescript_for() against MinimapStatePayload and commit the result."
)


class _FakeNode:
    def __init__(self, text, is_user):
        self.text = text
        self.is_user = is_user


class _FakeScene(QObject):
    scene_changed = Signal()

    def __init__(self):
        super().__init__()
        self.nodes = [_FakeNode("Hello there", True)]


class _FakeChatView:
    def __init__(self):
        self._scene = _FakeScene()

    def scene(self):
        return self._scene


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _snapshot() -> dict:
    bridge = MinimapBridge(_FakeChatView())
    states: list[str] = []
    bridge.stateChanged.connect(states.append)
    bridge.publish()
    return json.loads(states[-1])


class TestTheContractDescribesTheRealPayload:
    def test_a_real_bridge_snapshot_validates(self):
        errors = validate_payload(_snapshot(), MinimapStatePayload)

        assert errors == [], errors

    def test_the_nested_node_entry_carries_the_expected_fields(self):
        entry = _snapshot()["nodes"][0]

        assert set(entry.keys()) == {"id", "isUser", "preview"}


class TestValidatorActuallyRejects:
    @pytest.mark.parametrize(
        "mutate,expected_fragment",
        [
            (lambda p: p.pop("schemaVersion"), "missing required field"),
            (lambda p: p.pop("nodes"), "missing required field"),
            (lambda p: p.__setitem__("nodes", "not a list"), "expected array"),
            (lambda p: p["nodes"][0].pop("preview"), "missing required field"),
            (lambda p: p["nodes"][0].__setitem__("isUser", "not a bool"), "expected boolean"),
            (lambda p: p.__setitem__("surprise", "x"), "unexpected field"),
        ],
    )
    def test_mutation_is_caught(self, mutate, expected_fragment):
        payload = _snapshot()
        mutate(payload)

        errors = validate_payload(payload, MinimapStatePayload)

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
        fresh = schema_json_for(MinimapStatePayload, title="MinimapState")

        assert _read(_SCHEMA_FILE) == fresh, f"{_SCHEMA_FILE.name} is stale. {_REGENERATE_HINT}"

    def test_typescript_matches_regenerating_it_now(self):
        fresh = typescript_for(MinimapStatePayload, source=_TS_SOURCE_LABEL)

        assert _read(_TS_FILE) == fresh, f"{_TS_FILE.name} is stale. {_REGENERATE_HINT}"
