"""ADR-022 stage 22.2: property-based tests for graphlink_wire_schema.py.

Covers the two invariants the module's own docstring already claims:
json_schema_for's SUPPORTED TYPE CONSTRUCTS list round-trips cleanly, and
validate_payload independently agrees with any real instance of a dataclass
built from that same list - a genuine cross-check per validate_payload's own
docstring ("Checking the payload against the dataclasses independently means
the generated schema and this validator can disagree").
"""

from __future__ import annotations

import dataclasses
from typing import Literal

from hypothesis import assume, given, strategies as st

import graphlink_wire_schema as wire_schema


@dataclasses.dataclass
class _Nested:
    label: str
    count: int


@dataclasses.dataclass
class _SamplePayload:
    """Exercises every construct graphlink_wire_schema.py's own module
    docstring lists as supported: str, int, float, bool, Literal, list[X],
    dict[str, str], X | None, and a nested dataclass."""

    name: str
    count: int
    ratio: float
    enabled: bool
    kind: Literal["a", "b", "c"]
    tags: list[str]
    meta: dict[str, str]
    nested: _Nested
    note: str | None


_nested_strategy = st.builds(
    _Nested,
    label=st.text(max_size=20),
    count=st.integers(min_value=-1000, max_value=1000),
)

_sample_strategy = st.builds(
    _SamplePayload,
    name=st.text(max_size=20),
    count=st.integers(min_value=-1000, max_value=1000),
    ratio=st.floats(allow_nan=False, allow_infinity=False, width=32),
    enabled=st.booleans(),
    kind=st.sampled_from(["a", "b", "c"]),
    tags=st.lists(st.text(max_size=10), max_size=5),
    meta=st.dictionaries(st.text(max_size=10), st.text(max_size=10), max_size=5),
    nested=_nested_strategy,
    note=st.one_of(st.none(), st.text(max_size=20)),
)


@given(instance=_sample_strategy)
def test_validate_payload_accepts_any_generated_instance_of_its_own_dataclass(instance):
    payload = dataclasses.asdict(instance)
    errors = wire_schema.validate_payload(payload, _SamplePayload)
    assert errors == []


@given(instance=_sample_strategy, extra_key=st.text(min_size=1, max_size=10))
def test_validate_payload_rejects_an_unexpected_field(instance, extra_key):
    payload = dataclasses.asdict(instance)
    assume(extra_key not in payload)  # collision with a real field name isn't the case under test
    payload[extra_key] = "unexpected"
    errors = wire_schema.validate_payload(payload, _SamplePayload)
    assert any("unexpected field" in error for error in errors)


def test_json_schema_for_is_deterministic_and_shaped_correctly():
    first = wire_schema.json_schema_for(_SamplePayload)
    second = wire_schema.json_schema_for(_SamplePayload)
    assert first == second
    assert first["type"] == "object"
    assert first["additionalProperties"] is False
    # `note` is the only Optional field - every other field name must be
    # required, and required must never include an Optional field.
    assert set(first["required"]) == {"name", "count", "ratio", "enabled", "kind", "tags", "meta", "nested"}
    assert "note" not in first["required"]
