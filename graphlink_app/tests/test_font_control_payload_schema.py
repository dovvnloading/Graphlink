"""The font-control wire contract: staleness of the generated artifacts, and
that the contract actually describes the REAL payload.

See test_composer_payload_schema.py's module docstring for why both matter;
the shared codegen mechanism itself is exhaustively covered there and not
duplicated here.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from graphlink_font_control_bridge import FontControlBridge
from graphlink_font_control_payload import FontControlStatePayload
from graphlink_island_codegen import schema_json_for, typescript_for
from graphlink_island_schema import validate_payload

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATED = _REPO_ROOT / "web_ui" / "src" / "lib" / "bridge-core" / "generated"
_SCHEMA_FILE = _GENERATED / "font-control-state.schema.json"
_TS_FILE = _GENERATED / "font-control-state.ts"

_TS_SOURCE_LABEL = "graphlink_app/graphlink_font_control_payload.py::FontControlStatePayload"

_REGENERATE_HINT = (
    "Regenerate with graphlink_island_codegen.py's schema_json_for()/"
    "typescript_for() against FontControlStatePayload and commit the result."
)


class _FakeScene:
    def setFontFamily(self, family):
        pass

    def setFontSize(self, size):
        pass

    def setFontColor(self, color):
        pass


class _FakeChatView:
    def scene(self):
        return _FakeScene()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _snapshot() -> dict:
    bridge = FontControlBridge(_FakeChatView())
    states: list[str] = []
    bridge.stateChanged.connect(states.append)
    bridge.publish()
    return json.loads(states[-1])


class TestTheContractDescribesTheRealPayload:
    def test_a_real_bridge_snapshot_validates(self):
        errors = validate_payload(_snapshot(), FontControlStatePayload)

        assert errors == [], errors


class TestValidatorActuallyRejects:
    @pytest.mark.parametrize(
        "mutate,expected_fragment",
        [
            (lambda p: p.pop("schemaVersion"), "missing required field"),
            (lambda p: p.pop("fontFamilies"), "missing required field"),
            (lambda p: p.__setitem__("sizeMin", "not an int"), "expected integer"),
            (lambda p: p["fontFamilies"].__setitem__(0, 123), "expected string"),
            (lambda p: p.__setitem__("surprise", "x"), "unexpected field"),
        ],
    )
    def test_mutation_is_caught(self, mutate, expected_fragment):
        payload = _snapshot()
        mutate(payload)

        errors = validate_payload(payload, FontControlStatePayload)

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
        fresh = schema_json_for(FontControlStatePayload, title="FontControlState")

        assert _read(_SCHEMA_FILE) == fresh, f"{_SCHEMA_FILE.name} is stale. {_REGENERATE_HINT}"

    def test_typescript_matches_regenerating_it_now(self):
        fresh = typescript_for(FontControlStatePayload, source=_TS_SOURCE_LABEL)

        assert _read(_TS_FILE) == fresh, f"{_TS_FILE.name} is stale. {_REGENERATE_HINT}"
