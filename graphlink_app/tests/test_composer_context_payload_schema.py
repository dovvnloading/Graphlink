"""The composer-context wire contract: staleness of the generated artifacts,
and that the contract actually describes the REAL payload (including its
nested anchor/items).

See test_composer_payload_schema.py's module docstring for why both matter;
the shared codegen mechanism itself is exhaustively covered there and not
duplicated here.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from graphlink_composer_context_bridge import ComposerContextBridge
from graphlink_composer_context_payload import ComposerContextStatePayload
from graphlink_island_codegen import schema_json_for, typescript_for
from graphlink_island_schema import validate_payload

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATED = _REPO_ROOT / "web_ui" / "src" / "lib" / "bridge-core" / "generated"
_SCHEMA_FILE = _GENERATED / "composer-context-state.schema.json"
_TS_FILE = _GENERATED / "composer-context-state.ts"

_TS_SOURCE_LABEL = "graphlink_app/graphlink_composer_context_payload.py::ComposerContextStatePayload"

_REGENERATE_HINT = (
    "Regenerate with graphlink_island_codegen.py's schema_json_for()/"
    "typescript_for() against ComposerContextStatePayload and commit the result."
)


class _FakeComposerBridge:
    def removeContextItem(self, item_id):
        pass


_CONTEXT = {
    "anchor": {"id": "node-1", "label": "Chart analysis", "type": "ChatNode"},
    "items": [{"id": "attachment-0", "name": "analysis.csv", "kind": "document", "tokenCount": 42}],
    "totalTokens": 42,
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _snapshot(context: dict | None = None) -> dict:
    bridge = ComposerContextBridge(_FakeComposerBridge())
    bridge.open(context if context is not None else _CONTEXT)
    states: list[str] = []
    bridge.stateChanged.connect(states.append)
    bridge.publish()
    return json.loads(states[-1])


class TestTheContractDescribesTheRealPayload:
    def test_a_real_bridge_snapshot_validates(self):
        errors = validate_payload(_snapshot(), ComposerContextStatePayload)

        assert errors == [], errors

    def test_the_nested_item_carries_the_expected_fields(self):
        item = _snapshot()["items"][0]

        assert set(item.keys()) == {"id", "name", "kind", "tokenCount"}

    def test_a_snapshot_with_no_anchor_also_validates(self):
        payload = _snapshot({"anchor": None, "items": [], "totalTokens": 0})

        assert payload["anchor"] is None
        errors = validate_payload(payload, ComposerContextStatePayload)
        assert errors == [], errors


class TestValidatorActuallyRejects:
    @pytest.mark.parametrize(
        "mutate,expected_fragment",
        [
            (lambda p: p.pop("schemaVersion"), "missing required field"),
            (lambda p: p.pop("items"), "missing required field"),
            (lambda p: p.__setitem__("items", "not a list"), "expected array"),
            (lambda p: p["items"][0].pop("tokenCount"), "missing required field"),
            (lambda p: p.__setitem__("surprise", "x"), "unexpected field"),
        ],
    )
    def test_mutation_is_caught(self, mutate, expected_fragment):
        payload = _snapshot()
        mutate(payload)

        errors = validate_payload(payload, ComposerContextStatePayload)

        assert errors, "mutation was not caught - the validator is too permissive"
        assert any(expected_fragment in error for error in errors), (
            f"expected an error containing {expected_fragment!r}, got {errors}"
        )

    def test_a_malformed_anchor_is_caught(self):
        payload = _snapshot()
        payload["anchor"].pop("label")

        errors = validate_payload(payload, ComposerContextStatePayload)

        assert errors, "a malformed nested anchor was not caught"
        assert any("label" in error and "missing required field" in error for error in errors)


class TestGeneratedArtifactsAreNotStale:
    def test_schema_file_exists(self):
        assert _SCHEMA_FILE.is_file(), f"{_SCHEMA_FILE} is missing. {_REGENERATE_HINT}"

    def test_ts_file_exists(self):
        assert _TS_FILE.is_file(), f"{_TS_FILE} is missing. {_REGENERATE_HINT}"

    def test_schema_matches_regenerating_it_now(self):
        fresh = schema_json_for(ComposerContextStatePayload, title="ComposerContextState")

        assert _read(_SCHEMA_FILE) == fresh, f"{_SCHEMA_FILE.name} is stale. {_REGENERATE_HINT}"

    def test_typescript_matches_regenerating_it_now(self):
        fresh = typescript_for(ComposerContextStatePayload, source=_TS_SOURCE_LABEL)

        assert _read(_TS_FILE) == fresh, f"{_TS_FILE.name} is stale. {_REGENERATE_HINT}"
