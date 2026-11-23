"""Check that operational and semantic outcomes cannot be interchanged."""

import io

import pytest

from stage import TARGET, classify_report, read_bounded


def test_accepted_measurement_requires_both_kernel_verdicts():
    facts = {"statementMatches": True, "axioms": []}
    log = (
        b"nanoda kernel accepts the solution\n"
        b"Lean default kernel accepts the solution\n"
        b"Your solution is okay!\n"
    )
    assert classify_report(facts, 0, log) == ("accepted", ["nanoda", "Lean default"])
    with pytest.raises(ValueError):
        classify_report(facts, 0, log.split(b"\n", 1)[1])
    with pytest.raises(ValueError):
        classify_report(facts, 2, log)


@pytest.mark.parametrize(
    ("facts", "diagnostic", "outcome"),
    [
        ({"statementMatches": True, "axioms": ["sorryAx"]},
         "error: Illegal axiom detected: 'sorryAx'", "forbidden_axiom"),
        ({"statementMatches": False, "axioms": []},
         f"error: Challenge and solution theorem statement do not match: '{TARGET}'", "statement_mismatch"),
    ],
)
def test_negative_measurement_requires_the_specific_comparator_rejection(facts, diagnostic, outcome):
    assert classify_report(facts, 1, diagnostic.encode()) == (outcome, [])
    with pytest.raises(ValueError):
        classify_report(facts, 1, b"error: missing checker")
    with pytest.raises(ValueError):
        classify_report(facts, 2, diagnostic.encode())


def test_excess_input_is_rejected_in_full():
    assert read_bounded(io.BytesIO(b"abc"), 3) == b"abc"
    with pytest.raises(ValueError):
        read_bounded(io.BytesIO(b"abcd"), 3)
