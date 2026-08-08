"""Golden-fixture loading for the ADR-016 stage 16.5 eval harness.

A fixture is a plain JSON file under ``backend/evals/fixtures/<kind>/``. See
backend/evals/README.md for the exact on-disk shape and how to add one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"

# The three ADR-016 eval dimensions - see backend/evals/__init__.py's own
# docstring for what real code (or, for agent_build, honest stub) backs each.
VALID_KINDS = ("chart", "structured_output", "agent_build")


@dataclass(frozen=True)
class EvalFixture:
    name: str
    kind: str  # one of VALID_KINDS
    input: dict
    scripted_response: str | None
    expected: dict | None


def load_fixtures(kind: str) -> list[EvalFixture]:
    """Reads every ``*.json`` file under ``backend/evals/fixtures/<kind>/``
    and returns one :class:`EvalFixture` per file, sorted by filename for a
    stable, reproducible run order. A ``kind`` directory that does not exist
    yet is not an error - it simply contributes zero fixtures (mirrors the
    agent_build dimension today, which ships scaffolding but no goldens are
    required for it to be meaningful)."""
    if kind not in VALID_KINDS:
        raise ValueError(f"Unknown eval fixture kind: {kind!r} (expected one of {VALID_KINDS})")

    kind_dir = FIXTURES_ROOT / kind
    if not kind_dir.is_dir():
        return []

    fixtures: list[EvalFixture] = []
    for path in sorted(kind_dir.glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        fixtures.append(
            EvalFixture(
                name=raw.get("name", path.stem),
                kind=kind,
                input=raw.get("input", {}) or {},
                scripted_response=raw.get("scripted_response"),
                expected=raw.get("expected"),
            )
        )
    return fixtures
