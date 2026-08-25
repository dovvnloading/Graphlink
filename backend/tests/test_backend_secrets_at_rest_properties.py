"""ADR-022 stage 22.2: property-based tests for graphlink_secrets.py's
protect()/unprotect() pair.

Not in mutation-testing scope (stage 22.4) - DPAPI is Windows-only, and
scoping the nightly mutmut job to platform-independent modules keeps it off
the expensive Windows runner tier (see pyproject.toml's own [tool.mutmut]
comment). Lives in backend/tests/ alongside the existing hand-written
test_backend_secrets_at_rest.py, not mutation_tests/, since it has no need
to run inside mutmut's lightweight sandbox.
"""

from __future__ import annotations

import sys

import pytest
from hypothesis import given, strategies as st

import graphlink_secrets


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only - see graphlink_secrets.py's own module docstring")
@given(value=st.text(max_size=200))
def test_unprotect_of_protect_round_trips_on_a_dpapi_capable_machine(value):
    if not graphlink_secrets.dpapi_available():
        pytest.skip("DPAPI probe failed on this machine (locked-down environment, missing crypto provider, ...)")
    assert graphlink_secrets.unprotect(graphlink_secrets.protect(value)) == value


@given(value=st.text(max_size=200))
def test_protect_is_idempotent_so_a_migration_pass_never_double_wraps(value):
    once = graphlink_secrets.protect(value)
    twice = graphlink_secrets.protect(once)
    assert once == twice


def test_protect_of_empty_string_stays_empty():
    assert graphlink_secrets.protect("") == ""


def test_unprotect_of_empty_string_stays_empty():
    assert graphlink_secrets.unprotect("") == ""
