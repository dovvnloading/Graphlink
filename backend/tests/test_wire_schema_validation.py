"""ADR-003 stage 3.2 review-fix: dedicated coverage for graphlink_wire_schema.py's
validate_payload()/_validate_value(), specifically the recursive list[X]/
dict[str,X]/nested-dataclass/Literal branches.

Before this file, validate_payload had ZERO test coverage anywhere in the
repo: contracts/tests/test_generated_artifacts.py only exercises
json_schema_for/SchemaGenerationError (schema GENERATION), never the VALUE
validator, and the only real caller today - backend/events.py's
_validate_intent_args, reached from backend/tests/test_event_bus.py's
args_schema tests - only ever validates flat, single-string dataclasses
(ShowMessageArgs, ExecutePluginArgs), so even the production call path never
exercised these branches. That gap becomes load-bearing the moment a future
intent (very plausibly during the scene topic's own migration, per this
ADR's own "topic-by-topic, scene last" plan - e.g. moveNodes' positions list,
or addImageNode's several fields) gets an args_schema with a list/dict/
nested field.

graphlink_wire_schema.py lives at the repo root (not backend/), matching the
existing precedent of backend/tests/test_process_env_allowlist.py testing
the repo-root graphlink_process_env.py from here rather than a separate
top-level tests/ directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from graphlink_wire_schema import validate_payload


@dataclass
class _Address:
    city: str
    zip_code: str | None = None


@dataclass
class _Person:
    name: str
    age: int
    role: Literal["admin", "member"]
    tags: list[str]
    address: _Address
    metadata: dict[str, str]
    nickname: str | None = None


def _valid_payload() -> dict:
    return {
        "name": "Ada",
        "age": 30,
        "role": "admin",
        "tags": ["a", "b"],
        "address": {"city": "London", "zip_code": "SW1"},
        "metadata": {"team": "core"},
    }


def test_a_fully_valid_payload_has_no_errors():
    assert validate_payload(_valid_payload(), _Person) == []


def test_missing_required_field_is_reported_at_its_own_path():
    payload = _valid_payload()
    del payload["name"]
    errors = validate_payload(payload, _Person)
    assert any("$.name: missing required field" in e for e in errors)


def test_null_for_a_required_field_is_rejected():
    payload = _valid_payload()
    payload["age"] = None
    errors = validate_payload(payload, _Person)
    assert any("$.age" in e and "null is not allowed" in e for e in errors)


def test_optional_field_omitted_is_not_an_error():
    payload = _valid_payload()
    assert "nickname" not in payload
    assert validate_payload(payload, _Person) == []


def test_optional_field_supplied_is_validated_like_any_other():
    payload = _valid_payload()
    payload["nickname"] = 12345
    errors = validate_payload(payload, _Person)
    assert any("$.nickname" in e and "expected string" in e for e in errors)


def test_wrong_primitive_type_is_rejected_with_the_type_name_not_the_value():
    payload = _valid_payload()
    payload["age"] = "thirty"
    errors = validate_payload(payload, _Person)
    assert errors == ["$.age: expected integer, got str"]


def test_bool_is_not_silently_accepted_as_int_despite_being_a_subclass():
    payload = _valid_payload()
    payload["age"] = True
    errors = validate_payload(payload, _Person)
    assert any("$.age" in e and "expected integer" in e for e in errors)


def test_literal_field_accepts_an_allowed_value():
    payload = _valid_payload()
    payload["role"] = "member"
    assert validate_payload(payload, _Person) == []


def test_literal_field_rejects_a_disallowed_value_without_echoing_it_raw():
    # Review-fix: the Literal branch used to embed the raw submitted value
    # via !r (an inconsistency with every other branch's type-name-only
    # messages) - this pins the corrected, consistent shape.
    payload = _valid_payload()
    payload["role"] = "superadmin"
    errors = validate_payload(payload, _Person)
    assert errors == ["$.role: not one of ['admin', 'member'], got str"]
    assert not any("superadmin" in e for e in errors)


def test_list_field_rejects_a_non_list_value():
    payload = _valid_payload()
    payload["tags"] = "not-a-list"
    errors = validate_payload(payload, _Person)
    assert errors == ["$.tags: expected array, got str"]


def test_list_field_reports_a_wrong_typed_element_at_its_own_indexed_path():
    payload = _valid_payload()
    payload["tags"] = ["ok", 42, "also-ok"]
    errors = validate_payload(payload, _Person)
    assert errors == ["$.tags[1]: expected string, got int"]


def test_dict_field_rejects_a_non_dict_value():
    payload = _valid_payload()
    payload["metadata"] = ["not", "a", "dict"]
    errors = validate_payload(payload, _Person)
    assert errors == ["$.metadata: expected object, got list"]


def test_dict_field_rejects_a_non_string_key():
    # A real WS frame can never carry a non-string JSON object key, but
    # validate_payload is also called directly (e.g. ADR-003 stage 3.2's
    # _validate_intent_args builds its own dict from zip()), so a caller
    # bypassing JSON entirely can still hand it one - this proves the guard
    # holds even then, not only for JSON-sourced payloads.
    payload = _valid_payload()
    payload["metadata"] = {7: "seven"}
    errors = validate_payload(payload, _Person)
    assert any("key 7 is not a string" in e for e in errors)


def test_dict_field_reports_a_wrong_typed_value_at_its_own_key_path():
    payload = _valid_payload()
    payload["metadata"] = {"team": 123}
    errors = validate_payload(payload, _Person)
    assert errors == ["$.metadata.team: expected string, got int"]


def test_nested_dataclass_field_rejects_a_non_dict_value():
    payload = _valid_payload()
    payload["address"] = "not-an-object"
    errors = validate_payload(payload, _Person)
    assert errors == ["$.address: expected object, got str"]


def test_nested_dataclass_field_reports_a_missing_inner_field_at_the_nested_path():
    payload = _valid_payload()
    del payload["address"]["city"]
    errors = validate_payload(payload, _Person)
    assert errors == ["$.address.city: missing required field"]


def test_nested_dataclass_optional_inner_field_can_be_omitted():
    payload = _valid_payload()
    del payload["address"]["zip_code"]
    assert validate_payload(payload, _Person) == []


def test_nested_dataclass_reports_a_wrong_typed_inner_field():
    payload = _valid_payload()
    payload["address"]["city"] = 99
    errors = validate_payload(payload, _Person)
    assert errors == ["$.address.city: expected string, got int"]


def test_unexpected_field_not_on_the_dataclass_is_reported():
    payload = _valid_payload()
    payload["unexpected_extra_field"] = "surprise"
    errors = validate_payload(payload, _Person)
    assert any("unexpected field not present in _Person" in e for e in errors)


def test_multiple_independent_errors_are_all_reported_not_just_the_first():
    payload = _valid_payload()
    payload["age"] = "not a number"
    payload["role"] = "bogus"
    payload["tags"] = 5
    errors = validate_payload(payload, _Person)
    assert len(errors) == 3


def test_top_level_payload_that_is_not_a_dict_at_all_is_rejected():
    assert validate_payload(["not", "a", "dict"], _Person) == [
        "$: expected object, got list"
    ]
