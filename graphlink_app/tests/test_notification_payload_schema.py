"""The notification wire contract: staleness of the generated artifacts, and
- more importantly - that the contract actually describes the REAL payload.

See test_composer_payload_schema.py's module docstring for why both matter.
The shared codegen CLI mechanism (--check/--write, the generic schema
generator's refuse-to-guess rules) is already exhaustively covered there and
not duplicated here.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from graphlink_island_codegen import schema_json_for, typescript_for
from graphlink_island_schema import validate_payload
from graphlink_notification_bridge import NotificationBridge
from graphlink_notification_payload import NotificationStatePayload

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATED = _REPO_ROOT / "web_ui" / "src" / "lib" / "bridge-core" / "generated"
_SCHEMA_FILE = _GENERATED / "notification-state.schema.json"
_TS_FILE = _GENERATED / "notification-state.ts"

_TS_SOURCE_LABEL = "graphlink_app/graphlink_notification_payload.py::NotificationStatePayload"

_REGENERATE_HINT = (
    "Regenerate with graphlink_island_codegen.py's schema_json_for()/"
    "typescript_for() against NotificationStatePayload and commit the result."
)


class _Window:
    def should_show_notification(self, msg_type):
        return True


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _snapshot(message=None, duration_ms=3000, msg_type="info") -> dict:
    bridge = NotificationBridge(_Window())
    states: list[str] = []
    bridge.stateChanged.connect(states.append)
    if message is not None:
        bridge.show_message(message, duration_ms, msg_type)
    else:
        bridge.ready()
    return json.loads(states[-1])


class TestTheContractDescribesTheRealPayload:
    def test_the_initial_hidden_snapshot_validates(self):
        errors = validate_payload(_snapshot(), NotificationStatePayload)

        assert errors == [], errors

    @pytest.mark.parametrize("msg_type", ["info", "success", "warning", "error"])
    def test_a_shown_snapshot_validates_for_every_real_type(self, msg_type):
        errors = validate_payload(
            _snapshot("A message.", 5000, msg_type), NotificationStatePayload
        )

        assert errors == [], errors

    def test_an_unrecognized_type_still_validates_after_the_bridges_own_fallback(self):
        # The bridge normalizes unknown types to "info" before publishing -
        # this proves the CONTRACT itself only ever sees the 4 known literals.
        errors = validate_payload(
            _snapshot("A message.", 5000, "totally_unknown"), NotificationStatePayload
        )

        assert errors == [], errors


class TestValidatorActuallyRejects:
    @pytest.mark.parametrize(
        "mutate,expected_fragment",
        [
            (lambda p: p.pop("message"), "missing required field"),
            (lambda p: p.__setitem__("visible", "yes"), "expected boolean"),
            (lambda p: p.__setitem__("msgType", "bogus"), "is not one of"),
            (lambda p: p.__setitem__("surprise", "x"), "unexpected field"),
        ],
    )
    def test_mutation_is_caught(self, mutate, expected_fragment):
        payload = _snapshot("A message.", 5000, "info")
        mutate(payload)

        errors = validate_payload(payload, NotificationStatePayload)

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
        fresh = schema_json_for(NotificationStatePayload, title="NotificationState")

        assert _read(_SCHEMA_FILE) == fresh, f"{_SCHEMA_FILE.name} is stale. {_REGENERATE_HINT}"

    def test_typescript_matches_regenerating_it_now(self):
        fresh = typescript_for(NotificationStatePayload, source=_TS_SOURCE_LABEL)

        assert _read(_TS_FILE) == fresh, f"{_TS_FILE.name} is stale. {_REGENERATE_HINT}"
