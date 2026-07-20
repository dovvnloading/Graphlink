"""The token-counter wire contract: staleness of the generated artifacts, and
- more importantly - that the contract actually describes the REAL payload.

See test_composer_payload_schema.py's module docstring for why both matter
and why they live together. This file covers the same two guarantees for the
much smaller token-counter payload; the shared codegen CLI mechanism itself
(--check/--write behavior, the generic schema generator's refuse-to-guess
rules) is already exhaustively covered there and not duplicated here.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from graphlink_island_codegen import schema_json_for, typescript_for
from graphlink_island_schema import validate_payload
from graphlink_token_counter_bridge import TokenCounterBridge
from graphlink_token_counter_payload import TokenCounterStatePayload

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATED = _REPO_ROOT / "web_ui" / "src" / "lib" / "bridge-core" / "generated"
_SCHEMA_FILE = _GENERATED / "token-counter-state.schema.json"
_TS_FILE = _GENERATED / "token-counter-state.ts"

_TS_SOURCE_LABEL = "graphlink_app/graphlink_token_counter_payload.py::TokenCounterStatePayload"

_REGENERATE_HINT = (
    "Regenerate with graphlink_island_codegen.py's schema_json_for()/"
    "typescript_for() against TokenCounterStatePayload and commit the result."
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _snapshot(**counts) -> dict:
    bridge = TokenCounterBridge()
    states: list[str] = []
    bridge.stateChanged.connect(states.append)
    if counts:
        bridge.update_counts(**counts)
    else:
        bridge.ready()
    return json.loads(states[-1])


class TestTheContractDescribesTheRealPayload:
    def test_a_real_bridge_snapshot_validates(self):
        errors = validate_payload(_snapshot(), TokenCounterStatePayload)

        assert errors == [], errors

    def test_a_snapshot_with_real_counts_validates(self):
        errors = validate_payload(
            _snapshot(input_tokens=120, output_tokens=340, context_tokens=8000, total_tokens=8460),
            TokenCounterStatePayload,
        )

        assert errors == [], errors


class TestValidatorActuallyRejects:
    @pytest.mark.parametrize(
        "mutate,expected_fragment",
        [
            (lambda p: p.pop("inputTokens"), "missing required field"),
            (lambda p: p.__setitem__("totalTokens", "not a number"), "expected integer"),
            (lambda p: p.__setitem__("surprise", "x"), "unexpected field"),
        ],
    )
    def test_mutation_is_caught(self, mutate, expected_fragment):
        payload = _snapshot(input_tokens=5)
        mutate(payload)

        errors = validate_payload(payload, TokenCounterStatePayload)

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
        fresh = schema_json_for(TokenCounterStatePayload, title="TokenCounterState")

        assert _read(_SCHEMA_FILE) == fresh, f"{_SCHEMA_FILE.name} is stale. {_REGENERATE_HINT}"

    def test_typescript_matches_regenerating_it_now(self):
        fresh = typescript_for(TokenCounterStatePayload, source=_TS_SOURCE_LABEL)

        assert _read(_TS_FILE) == fresh, f"{_TS_FILE.name} is stale. {_REGENERATE_HINT}"
