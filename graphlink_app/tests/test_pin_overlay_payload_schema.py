"""The pin-overlay wire contract: staleness of the generated artifacts, and
that the contract actually describes the REAL payload (including its nested
PinRow list).

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

from graphlink_navigation_pins import NavigationPinStore
from graphlink_pin_overlay_bridge import PinOverlayBridge
from graphlink_pin_overlay_payload import PinOverlayStatePayload
from graphlink_island_codegen import schema_json_for, typescript_for
from graphlink_island_schema import validate_payload

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATED = _REPO_ROOT / "web_ui" / "src" / "lib" / "bridge-core" / "generated"
_SCHEMA_FILE = _GENERATED / "pin-overlay-state.schema.json"
_TS_FILE = _GENERATED / "pin-overlay-state.ts"

_TS_SOURCE_LABEL = "graphlink_app/graphlink_pin_overlay_payload.py::PinOverlayStatePayload"

_REGENERATE_HINT = (
    "Regenerate with graphlink_island_codegen.py's schema_json_for()/"
    "typescript_for() against PinOverlayStatePayload and commit the result."
)


class _FakeScene(QObject):
    selectionChanged = Signal()

    def __init__(self):
        super().__init__()
        self.pin_store = NavigationPinStore()
        self.pin_store.add(pin_id="p1", title="A pin", note="a note", x=0.0, y=0.0)

    def selectedItems(self):
        return []

    def _navigation_pin_item(self, pin_id):
        return None


class _FakeChatView:
    def __init__(self):
        self._scene = _FakeScene()

    def scene(self):
        return self._scene


class _FakeController:
    def __init__(self, draft=None, draft_is_new=False):
        self.draft = draft
        self.draft_is_new = draft_is_new


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _snapshot(controller=None) -> dict:
    bridge = PinOverlayBridge(_FakeChatView(), controller or _FakeController())
    states: list[str] = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    return json.loads(states[-1])


def _snapshot_with_draft() -> dict:
    store = NavigationPinStore()
    record = store.add(pin_id="p2", title="Drafted", note="draft note", x=0.0, y=0.0)
    controller = _FakeController(draft=record, draft_is_new=True)
    return _snapshot(controller)


class TestTheContractDescribesTheRealPayload:
    def test_a_real_bridge_snapshot_validates(self):
        errors = validate_payload(_snapshot(), PinOverlayStatePayload)

        assert errors == [], errors

    def test_the_nested_rows_carry_the_expected_fields(self):
        row = _snapshot()["rows"][0]

        assert set(row.keys()) == {"id", "title", "note"}

    def test_a_snapshot_with_an_active_draft_validates(self):
        payload = _snapshot_with_draft()

        assert payload["draft"] == {
            "pinId": "p2",
            "title": "Drafted",
            "note": "draft note",
            "isNew": True,
        }
        errors = validate_payload(payload, PinOverlayStatePayload)
        assert errors == [], errors


class TestValidatorActuallyRejects:
    @pytest.mark.parametrize(
        "mutate,expected_fragment",
        [
            (lambda p: p.pop("schemaVersion"), "missing required field"),
            (lambda p: p.pop("rows"), "missing required field"),
            (lambda p: p.__setitem__("rows", "not a list"), "expected array"),
            (lambda p: p["rows"][0].pop("title"), "missing required field"),
            (lambda p: p.__setitem__("surprise", "x"), "unexpected field"),
        ],
    )
    def test_mutation_is_caught(self, mutate, expected_fragment):
        payload = _snapshot()
        mutate(payload)

        errors = validate_payload(payload, PinOverlayStatePayload)

        assert errors, "mutation was not caught - the validator is too permissive"
        assert any(expected_fragment in error for error in errors), (
            f"expected an error containing {expected_fragment!r}, got {errors}"
        )

    def test_a_malformed_draft_is_caught(self):
        payload = _snapshot_with_draft()
        payload["draft"].pop("isNew")

        errors = validate_payload(payload, PinOverlayStatePayload)

        assert errors, "a malformed nested draft was not caught"
        assert any("isNew" in error and "missing required field" in error for error in errors)


class TestGeneratedArtifactsAreNotStale:
    def test_schema_file_exists(self):
        assert _SCHEMA_FILE.is_file(), f"{_SCHEMA_FILE} is missing. {_REGENERATE_HINT}"

    def test_ts_file_exists(self):
        assert _TS_FILE.is_file(), f"{_TS_FILE} is missing. {_REGENERATE_HINT}"

    def test_schema_matches_regenerating_it_now(self):
        fresh = schema_json_for(PinOverlayStatePayload, title="PinOverlayState")

        assert _read(_SCHEMA_FILE) == fresh, f"{_SCHEMA_FILE.name} is stale. {_REGENERATE_HINT}"

    def test_typescript_matches_regenerating_it_now(self):
        fresh = typescript_for(PinOverlayStatePayload, source=_TS_SOURCE_LABEL)

        assert _read(_TS_FILE) == fresh, f"{_TS_FILE.name} is stale. {_REGENERATE_HINT}"
