"""Review Lens evidence validation: verify actual new-side hunks, not LLM guesses.

These tests never use a remote GitHub repository or a real model.
"""
from __future__ import annotations

import json

import pytest

import api_provider
from graphlink_plugins.review_lens.evidence import (
    assess_model_location,
    index_visible_diff,
)
from graphlink_plugins.review_lens.review_engine import ReviewLensAgent


DIFF = "\n".join([
    "diff --git a/src/app.py b/src/app.py",
    "index 1111111..2222222 100644",
    "--- a/src/app.py",
    "+++ b/src/app.py",
    "@@ -10,3 +10,4 @@",
    " existing()",
    "-old()",
    "+dangerous()",
    "+harden()",
    " unchanged()",
    "@@ -52,1 +53,2 @@",
    " context()",
    "+later()",
    "diff --git a/tests/test_app.py b/tests/test_app.py",
    "new file mode 100644",
    "--- /dev/null",
    "+++ b/tests/test_app.py",
    "@@ -0,0 +1,2 @@",
    "+def test_app():",
    "+    assert True",
])


def _payload(**overrides):
    base = {
        "repo": "org/repo",
        "pr_number": 12,
        "pr_title": "Safety fix",
        "changed_files": 2,
        "additions": 6,
        "deletions": 1,
        "files": [{"path": "src/app.py"}, {"path": "tests/test_app.py"}],
        "diff_text": DIFF,
        "files_truncated": False,
        "diff_truncated": False,
    }
    base.update(overrides)
    return base


def _parsed(**overrides):
    review = {
        "title": "Code review",
        "overview": "Inspect the change.",
        "confidence": "high",
        "category_scores": {k: 90 for k in [
            "correctness", "reliability", "security", "maintainability",
            "readability", "testing", "performance", "architecture",
        ]},
        "review_findings": [],
        "errors_found": [],
    }
    review.update(overrides)
    return review


def _finding(path="src/app.py", line=11, severity="high", title="Example finding"):
    return {
        "severity": severity, "category": "reliability",
        "path": path, "line": line, "title": title,
        "evidence": "Model believes execution may fail.",
        "impact": "A test could fail.",
        "recommendation": "Guard the call.",
    }


def _error(path="src/app.py", line=11, severity="critical", title="Example error"):
    return {
        "severity": severity, "kind": "runtime",
        "path": path, "line": line, "title": title,
        "evidence": "Model thinks this throws.",
        "fix": "Handle the exception.",
    }


def _review(monkeypatch, **overrides):
    reply = json.dumps(_parsed(**overrides))
    monkeypatch.setattr(api_provider, "chat", lambda **_: {"message": {"content": reply}})
    return ReviewLensAgent().get_response(_payload())


def test_diff_evidence_new_side_line_numbers_multiple_hunks_and_files():
    index = index_visible_diff(DIFF)
    assert assess_model_location(index, "src/app.py", 10) == ("context", "existing()")
    assert assess_model_location(index, "src/app.py", 11) == ("added", "dangerous()")
    assert assess_model_location(index, "src/app.py", 12) == ("added", "harden()")
    assert assess_model_location(index, "src/app.py", 13) == ("context", "unchanged()")
    assert assess_model_location(index, "src/app.py", 53) == ("context", "context()")
    assert assess_model_location(index, "src/app.py", 54) == ("added", "later()")
    assert assess_model_location(index, "tests/test_app.py", 2) == ("added", "    assert True")
    assert assess_model_location(index, "src/app.py", 51)[0] == "unverified"


def test_diff_evidence_does_not_accept_a_path_or_line_from_the_model_alone():
    index = index_visible_diff(DIFF)
    assert assess_model_location(index, "src/ap.py", 11)[0] == "unverified"
    assert assess_model_location(index, "src/app.py.bak", 11)[0] == "unverified"
    assert assess_model_location(index, "../../secrets.py", 11)[0] == "unverified"
    assert assess_model_location(index, "src/app.py", 0)[0] == "file"
    assert assess_model_location(index, "not-in-diff.py", 0)[0] == "unverified"
    assert assess_model_location(index, "src/app.py", 10)[0] == "context"


def test_hunkless_fake_header_and_binary_or_deleted_file_cannot_anchor_an_error():
    fake = "\n".join([
        "diff --git a/x.py b/x.py",
        "--- a/x.py",
        "+++ b/x.py",
        "+password = 'secret' ",  # not in an @@ hunk
        "diff --git a/gone.py b/gone.py",
        "deleted file mode 100644",
        "--- a/gone.py",
        "+++ /dev/null",
        "@@ -1,1 +0,0 @@",
        "-removed",
        "diff --git a/image.png b/image.png",
        "Binary files differ",
    ])
    index = index_visible_diff(fake)
    for path, line in [("x.py", 1), ("gone.py", 1), ("image.png", 1)]:
        assert assess_model_location(index, path, line)[0] == "unverified"


def test_rename_uses_new_file_path_and_does_not_approve_old_name():
    diff = "\n".join([
        "diff --git a/old.py b/new.py", "similarity index 80%",
        "rename from old.py", "rename to new.py",
        "--- a/old.py", "+++ b/new.py",
        "@@ -1 +1 @@", "-old", "+new",
    ])
    index = index_visible_diff(diff)
    assert assess_model_location(index, "new.py", 1) == ("added", "new")
    assert assess_model_location(index, "old.py", 1)[0] == "unverified"


def test_spaces_and_quoted_paths_are_not_substring_matches():
    diff = "\n".join([
        'diff --git "a/my file.py" "b/my file.py"',
        '--- "a/my file.py"', '+++ "b/my file.py"',
        "@@ -0,0 +1 @@", "+name = 1",
    ])
    index = index_visible_diff(diff)
    assert assess_model_location(index, "my file.py", 1) == ("added", "name = 1")
    assert assess_model_location(index, "file.py", 1)[0] == "unverified"


def test_a_plus_prefixed_source_line_cannot_forge_a_new_file_header():
    # A code line that starts with two plus signs serializes as '+++ b/foo'.
    # This is *inside* the hunk, not a new +++ b/foo metadata header.
    diff = "\n".join([
        "diff --git a/src/app.py b/src/app.py",
        "--- a/src/app.py", "+++ b/src/app.py",
        "@@ -0,0 +1,3 @@",
        "+++ b/fake.py",
        "+actually_changed()",
        "+end()",
    ])
    index = index_visible_diff(diff)
    assert assess_model_location(index, "src/app.py", 1) == ("added", "++ b/fake.py")
    assert assess_model_location(index, "src/app.py", 2) == ("added", "actually_changed()")
    assert assess_model_location(index, "fake.py", 1)[0] == "unverified"


def test_model_claims_are_verified_against_added_hunks_and_show_actual_source(monkeypatch):
    result = _review(monkeypatch, errors_found=[_error()], review_findings=[
        _finding(path="tests/test_app.py", line=2, severity="medium"),
    ])
    assert result["fallback"] is False
    assert result["errors_found"][0]["severity"] == "critical"
    assert "Visible added line 11: dangerous()" in result["errors_found"][0]["evidence"]
    assert result["review_findings"][0]["tier"] == "yellow"
    assert "Visible added line 2:" in result["review_findings"][0]["evidence"]
    assert result["verdict"] == "not_ready"
    assert result["risk_level"] == "high"
    assert result["quality_score"] == 90


def test_hallucinated_critical_error_becomes_fyi_suggestion_not_a_verdict_gate(monkeypatch):
    result = _review(monkeypatch, errors_found=[
        _error("not/a/changed/file.py", 9999, title="Imaginary RCE"),
    ])
    assert result["errors_found"] == []
    assert result["review_findings"][0]["severity"] == "info"
    assert result["review_findings"][0]["tier"] == "gray"
    assert result["review_findings"][0]["category"] == "Needs Verification"
    assert "Imaginary RCE" in result["review_findings"][0]["title"]
    assert "not/a/changed/file.py:9999" in result["review_findings"][0]["evidence"]
    assert result["verdict"] == "none"
    assert result["risk_level"] == ""
    assert result["quality_score"] == 0
    assert result["category_scores"] == {}
    assert "Overall grading withheld" in result["quality_summary"]
    assert result["confidence"] == "low"


@pytest.mark.parametrize("line,title", [
    (10, "Unchanged context line"), (0, "File-only assertion"), (999, "Offscreen hunk"),
])
def test_unanchored_model_findings_are_provisional_and_do_not_raise_risk(monkeypatch, line, title):
    result = _review(monkeypatch, review_findings=[_finding(line=line, title=title)])
    assert result["errors_found"] == []
    item = result["review_findings"][0]
    assert item["severity"] == "info"
    assert item["tier"] == "gray"
    assert item["line"] == 0
    assert item["title"].startswith("Verify location")
    assert result["verdict"] == "none"


@pytest.mark.parametrize("flag", ["diff_truncated", "files_truncated"])
def test_partial_coverage_never_claims_a_strong_grade(monkeypatch, flag):
    result_json = json.dumps(_parsed())
    monkeypatch.setattr(api_provider, "chat", lambda **_: {"message": {"content": result_json}})
    result = ReviewLensAgent().get_response(_payload(**{flag: True}))
    assert result["fallback"] is False
    assert result["verdict"] == "none"
    assert result["quality_score"] == 0
    assert result["category_scores"] == {}
    assert result["confidence"] == "low"
    assert "truncated" in result["quality_summary"]
    assert "Not assessed" in result["quality_report_markdown"]
    assert "Assessment limited" in result["overview_markdown"]


def test_fallback_still_reports_heuristics_when_model_unavailable(monkeypatch):
    def no_model(**_):
        raise RuntimeError("no model")

    monkeypatch.setattr(api_provider, "chat", no_model)
    result = ReviewLensAgent().get_response(_payload(diff_text=(
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -0,0 +1 @@\n+eval(untrusted)\n"
    )))
    assert result["fallback"] is True
    assert result["verdict"] == "none"
    assert any("Dynamic code" in finding["title"] for finding in result["review_findings"])
