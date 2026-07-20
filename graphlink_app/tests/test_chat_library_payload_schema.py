"""The chat-library wire contract: staleness of the generated artifacts, and
that the contract actually describes the REAL payload (including its nested
ChatLibraryRow list).

See test_composer_payload_schema.py's module docstring for why both matter;
the shared codegen mechanism itself is exhaustively covered there and not
duplicated here.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from graphlink_chat_library_bridge import ChatLibraryBridge
from graphlink_chat_library_payload import ChatLibraryStatePayload
from graphlink_island_codegen import schema_json_for, typescript_for
from graphlink_island_schema import validate_payload

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATED = _REPO_ROOT / "web_ui" / "src" / "lib" / "bridge-core" / "generated"
_SCHEMA_FILE = _GENERATED / "chat-library-state.schema.json"
_TS_FILE = _GENERATED / "chat-library-state.ts"

_TS_SOURCE_LABEL = "graphlink_app/graphlink_chat_library_payload.py::ChatLibraryStatePayload"

_REGENERATE_HINT = (
    "Regenerate with graphlink_island_codegen.py's schema_json_for()/"
    "typescript_for() against ChatLibraryStatePayload and commit the result."
)


class _FakeDatabase:
    def get_all_chats(self):
        return [(1, "A chat", "2026-07-01 09:30:00", "2026-07-05 14:00:00")]


class _FakeSessionManager:
    def __init__(self):
        self.db = _FakeDatabase()
        self.window = None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _snapshot() -> dict:
    bridge = ChatLibraryBridge(_FakeSessionManager(), library_dialog=None)
    states: list[str] = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    return json.loads(states[-1])


class TestTheContractDescribesTheRealPayload:
    def test_a_real_bridge_snapshot_validates(self):
        errors = validate_payload(_snapshot(), ChatLibraryStatePayload)

        assert errors == [], errors

    def test_the_nested_rows_carry_the_expected_fields(self):
        row = _snapshot()["rows"][0]

        assert set(row.keys()) == {"id", "title", "createdLabel", "updatedLabel"}


class TestValidatorActuallyRejects:
    @pytest.mark.parametrize(
        "mutate,expected_fragment",
        [
            (lambda p: p.pop("schemaVersion"), "missing required field"),
            (lambda p: p.pop("rows"), "missing required field"),
            (lambda p: p.__setitem__("rows", "not a list"), "expected array"),
            (lambda p: p["rows"][0].pop("title"), "missing required field"),
            (lambda p: p["rows"][0].__setitem__("id", "not a number"), "expected integer"),
            (lambda p: p.__setitem__("surprise", "x"), "unexpected field"),
        ],
    )
    def test_mutation_is_caught(self, mutate, expected_fragment):
        payload = _snapshot()
        mutate(payload)

        errors = validate_payload(payload, ChatLibraryStatePayload)

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
        fresh = schema_json_for(ChatLibraryStatePayload, title="ChatLibraryState")

        assert _read(_SCHEMA_FILE) == fresh, f"{_SCHEMA_FILE.name} is stale. {_REGENERATE_HINT}"

    def test_typescript_matches_regenerating_it_now(self):
        fresh = typescript_for(ChatLibraryStatePayload, source=_TS_SOURCE_LABEL)

        assert _read(_TS_FILE) == fresh, f"{_TS_FILE.name} is stale. {_REGENERATE_HINT}"
