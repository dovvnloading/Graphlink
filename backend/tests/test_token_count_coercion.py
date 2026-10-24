"""Integer coercion of provider-reported counts that arrive as JSON numbers.

backend/providers/base.py's normalize_usage runs at every provider's done
event, AFTER the reply text has already streamed - an exception there
throws the whole reply away. graphlink_model_catalog's _as_int reads the
same kind of untrusted server number (Ollama model sizes and context
lengths). Both promise "a value that isn't a usable int becomes None",
and JSON can legitimately carry values int() refuses with something other
than TypeError/ValueError: `1e999` parses to float('inf'), and int(inf)
raises OverflowError."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

import graphlink_model_catalog as mc
from backend.providers.base import normalize_usage

# Values int() cannot turn into a finite count, each paired with the
# exception int() itself raises for it - the point of listing both is that
# the helpers must absorb every one of these, not just the ones that
# happened to be in the except tuple first.
_UNCONVERTIBLE = [
    pytest.param(json.loads("1e999"), id="json-1e999-is-inf"),       # OverflowError
    pytest.param(float("inf"), id="inf"),                            # OverflowError
    pytest.param(float("-inf"), id="-inf"),                          # OverflowError
    pytest.param(float("nan"), id="nan"),                            # ValueError
    pytest.param(Decimal("Infinity"), id="decimal-infinity"),        # OverflowError
    pytest.param("4.5", id="non-integral-string"),                   # ValueError
    pytest.param("lots", id="non-numeric-string"),                   # ValueError
    pytest.param({"tokens": 3}, id="object"),                        # TypeError
]


@pytest.mark.parametrize("value", _UNCONVERTIBLE)
def test_normalize_usage_turns_an_unconvertible_completion_count_into_none(value):
    assert normalize_usage(None, value) is None
    assert normalize_usage(12, value) == {"prompt_tokens": 12, "completion_tokens": None}


@pytest.mark.parametrize("value", _UNCONVERTIBLE)
def test_normalize_usage_turns_an_unconvertible_prompt_count_into_none(value):
    assert normalize_usage(value, None) is None
    assert normalize_usage(value, 7) == {"prompt_tokens": None, "completion_tokens": 7}


def test_normalize_usage_keeps_every_value_int_can_represent():
    assert normalize_usage(None, None) is None
    assert normalize_usage("42", 3.0) == {"prompt_tokens": 42, "completion_tokens": 3}
    # Huge but FINITE is still a real integer, not an overflow.
    assert normalize_usage(1e300, 0) == {"prompt_tokens": int(1e300), "completion_tokens": 0}


@pytest.mark.parametrize("value", _UNCONVERTIBLE)
def test_catalog_as_int_turns_an_unconvertible_value_into_none(value):
    assert mc._as_int(value) is None


def test_catalog_as_int_keeps_every_value_int_can_represent():
    assert mc._as_int(None) is None
    assert mc._as_int("4096") == 4096
    assert mc._as_int(8192.0) == 8192
    assert mc._as_int(1e300) == int(1e300)


def test_ollama_descriptor_survives_infinite_size_and_context_length():
    # One malformed model entry must not take the whole catalog listing
    # down with it - the descriptor is still built, just without the
    # unusable numbers.
    model = json.loads(
        '{"model": "llama3:8b", "size": 1e999,'
        ' "details": {"family": "llama", "context_length": 1e999},'
        ' "model_info": {"llama.context_length": -1e999}}'
    )

    descriptor = mc.ollama_descriptor(model)

    assert descriptor.model_id == "llama3:8b"
    assert descriptor.size_bytes is None
    assert descriptor.context_length is None
