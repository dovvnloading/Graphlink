"""The search-overlay wire contract: staleness of the generated artifacts, and
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

from graphlink_search_overlay_bridge import SearchOverlayBridge
from graphlink_search_overlay_payload import SearchOverlayStatePayload
from graphlink_island_codegen import schema_json_for, typescript_for
from graphlink_island_schema import validate_payload

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATED = _REPO_ROOT / "web_ui" / "src" / "lib" / "bridge-core" / "generated"
_SCHEMA_FILE = _GENERATED / "search-overlay-state.schema.json"
_TS_FILE = _GENERATED / "search-overlay-state.ts"

_TS_SOURCE_LABEL = "graphlink_app/graphlink_search_overlay_payload.py::SearchOverlayStatePayload"

_REGENERATE_HINT = (
    "Regenerate with graphlink_island_codegen.py's schema_json_for()/"
    "typescript_for() against SearchOverlayStatePayload and commit the result."
)


class _FakeScene:
    def find_items(self, text):
        return []

    def update_search_highlight(self, matches):
        pass


class _FakeChatView:
    def scene(self):
        return _FakeScene()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _snapshot() -> dict:
    bridge = SearchOverlayBridge(_FakeChatView())
    states: list[str] = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    return json.loads(states[-1])


class TestTheContractDescribesTheRealPayload:
    def test_a_real_bridge_snapshot_validates(self):
        errors = validate_payload(_snapshot(), SearchOverlayStatePayload)

        assert errors == [], errors


class TestValidatorActuallyRejects:
    @pytest.mark.parametrize(
        "mutate,expected_fragment",
        [
            (lambda p: p.pop("schemaVersion"), "missing required field"),
            (lambda p: p.pop("totalMatches"), "missing required field"),
            (lambda p: p.__setitem__("currentIndex", "not a number"), "expected integer"),
            (lambda p: p.__setitem__("surprise", "x"), "unexpected field"),
        ],
    )
    def test_mutation_is_caught(self, mutate, expected_fragment):
        payload = _snapshot()
        mutate(payload)

        errors = validate_payload(payload, SearchOverlayStatePayload)

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
        fresh = schema_json_for(SearchOverlayStatePayload, title="SearchOverlayState")

        assert _read(_SCHEMA_FILE) == fresh, f"{_SCHEMA_FILE.name} is stale. {_REGENERATE_HINT}"

    def test_typescript_matches_regenerating_it_now(self):
        fresh = typescript_for(SearchOverlayStatePayload, source=_TS_SOURCE_LABEL)

        assert _read(_TS_FILE) == fresh, f"{_TS_FILE.name} is stale. {_REGENERATE_HINT}"
