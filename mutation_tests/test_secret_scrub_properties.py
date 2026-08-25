"""ADR-022 stage 22.2: property-based tests for backend/secret_scrub.py.

Covers the module's own documented contracts: scrub() never mutates its
input, scrub_text/scrub are idempotent (a second pass changes nothing), and
a credential-shaped substring never survives scrub_text regardless of what
surrounding text it's embedded in.
"""

from __future__ import annotations

import copy

from hypothesis import given, strategies as st

from backend import secret_scrub


_json_scalar = st.one_of(st.none(), st.booleans(), st.integers(), st.floats(allow_nan=False, allow_infinity=False), st.text(max_size=20))
_json_value = st.recursive(
    _json_scalar,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=10), children, max_size=4),
    ),
    max_leaves=15,
)


@given(value=_json_value)
def test_scrub_does_not_mutate_its_input(value):
    original = copy.deepcopy(value)
    secret_scrub.scrub(value)
    assert value == original


@given(value=_json_value)
def test_scrub_is_idempotent(value):
    once = secret_scrub.scrub(value)
    twice = secret_scrub.scrub(once)
    assert once == twice


@given(text=st.text(max_size=100))
def test_scrub_text_is_idempotent(text):
    once = secret_scrub.scrub_text(text)
    twice = secret_scrub.scrub_text(once)
    assert once == twice


_ASCII_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


@given(
    prefix=st.text(max_size=15),
    suffix=st.text(max_size=15),
    # Must match the real pattern's own character class exactly
    # ([A-Za-z0-9_\-]{16,}) - a wider Unicode alphabet (e.g. a non-ASCII
    # letter category) would generate "secrets" the real regex was never
    # meant to catch, which is a bug in the test, not the source.
    secret_body=st.text(alphabet=_ASCII_ALNUM, min_size=16, max_size=30),
)
def test_scrub_text_redacts_an_openai_style_key_embedded_in_arbitrary_text(prefix, suffix, secret_body):
    secret = f"sk-{secret_body}"
    text = f"{prefix}{secret}{suffix}"
    result = secret_scrub.scrub_text(text)
    assert secret not in result


@given(key_suffix=st.sampled_from(["api_key", "API_KEY", "Access_Token", "password", "PRIVATE_KEY"]), value=st.text(min_size=1, max_size=20))
def test_scrub_redacts_any_value_under_a_secret_shaped_key_name(key_suffix, value):
    payload = {f"provider_{key_suffix}": value}
    result = secret_scrub.scrub(payload)
    assert result[f"provider_{key_suffix}"] == secret_scrub.REDACTED
