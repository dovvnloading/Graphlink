"""Direct unit tests for Review Lens's domain logic.

Mirrors backend/tests/test_gitlink_domain.py's own conventions:
  - api_provider.chat is monkeypatched to a plain lambda returning
    {"message": {"content": ...}};
  - GitHub REST calls are faked via a duck-typed stand-in client exposing
    `.request(url, params=None, ...)` plus `.build_headers(url)`, and the
    unified-diff download fakes `requests.get` (in the review_lens
    diff_fetch module's namespace) with a stand-in response exposing
    status_code/content - matching this codebase's established
    fake-response-object convention.

Covers graphlink_plugins/review_lens/pr_url.py (URL parsing),
diff_fetch.py (bundle assembly, truncation, error mapping), and
review_engine.py (normalization discipline, verdict gates, deterministic
fallback heuristics, get_response both paths, answer_question).
"""

from __future__ import annotations

import json

import pytest

import api_provider
from graphlink_plugins.review_lens import diff_fetch as diff_fetch_module
from graphlink_plugins.review_lens.diff_fetch import (
    MAX_DIFF_CHARS,
    _normalize_file_entry,
    fetch_pr_review_bundle,
)
from graphlink_plugins.review_lens.pr_url import canonical_pr_slug, parse_pr_url
from graphlink_plugins.review_lens.review_engine import (
    SEVERITY_TIERS,
    ReviewLensAgent,
    _group_files_for_walkthrough,
)


# =============================================================================
# pr_url.py
# =============================================================================


def test_parse_pr_url_accepts_bare_pr_url():
    assert parse_pr_url("https://github.com/octocat/Hello-World/pull/1347") == ("octocat", "Hello-World", 1347)


def test_parse_pr_url_tolerates_suffixes_query_and_fragment():
    assert parse_pr_url("https://github.com/o/r/pull/7/files") == ("o", "r", 7)
    assert parse_pr_url("https://github.com/o/r/pull/7/commits/") == ("o", "r", 7)
    assert parse_pr_url("https://github.com/o/r/pull/7/files?w=1#diff-abc") == ("o", "r", 7)
    assert parse_pr_url("  github.com/o/r/pull/42  ") == ("o", "r", 42)


def test_parse_pr_url_rejects_non_pr_links():
    for bad in (
        "",
        "not a url",
        "https://github.com/o/r/issues/12",
        "https://github.com/o/r/pulls",
        "https://github.com/o/r/pull/abc",
        "https://github.com/o/pull/12",
        "https://gitlab.com/o/r/pull/12",
        "https://evil-github.com/o/r/pull/12",
    ):
        with pytest.raises(RuntimeError):
            parse_pr_url(bad)


def test_canonical_pr_slug():
    assert canonical_pr_slug("o", "r", 9) == "o/r#9"


# =============================================================================
# diff_fetch.py
# =============================================================================


class _FakeClient:
    """Duck-typed GitHubRestClient stand-in: canned metadata + file pages."""

    def __init__(self, metadata, file_pages):
        self._metadata = metadata
        self._file_pages = file_pages
        self.requested_urls = []

    def build_headers(self, url=None):
        return {"Accept": "application/vnd.github+json"}

    def request(self, url, params=None, *, expect_json=True, timeout=25):
        self.requested_urls.append(url)
        if url.endswith("/files"):
            page = (params or {}).get("page", 1)
            return self._file_pages[page - 1] if page - 1 < len(self._file_pages) else []
        return self._metadata


class _FakeDiffResponse:
    def __init__(self, text, status_code=200):
        self.content = text.encode("utf-8")
        self.status_code = status_code


def _metadata(**overrides):
    base = {
        "title": "Add health check",
        "state": "open",
        "html_url": "https://github.com/o/r/pull/3",
        "base": {"ref": "main"},
        "head": {"ref": "feature/health"},
        "additions": 10,
        "deletions": 2,
        "changed_files": 1,
    }
    base.update(overrides)
    return base


def _run_bundle(monkeypatch, client, diff_text="diff --git a/x.py b/x.py\n+x = 1\n"):
    monkeypatch.setattr(
        diff_fetch_module.requests, "get",
        lambda url, headers=None, timeout=None: _FakeDiffResponse(diff_text),
    )
    return fetch_pr_review_bundle(client, "o", "r", 3)


def test_fetch_bundle_assembles_metadata_files_and_diff(monkeypatch):
    client = _FakeClient(
        _metadata(),
        [[{"filename": "x.py", "status": "added", "additions": 10, "deletions": 0, "patch": "@@ x"}]],
    )
    bundle = _run_bundle(monkeypatch, client)
    assert bundle["repo"] == "o/r"
    assert bundle["pr_number"] == 3
    assert bundle["pr_title"] == "Add health check"
    assert bundle["base_ref"] == "main"
    assert bundle["head_ref"] == "feature/health"
    assert bundle["files"][0]["path"] == "x.py"
    assert bundle["files"][0]["status"] == "added"
    assert "+x = 1" in bundle["diff_text"]
    assert bundle["diff_truncated"] is False
    assert bundle["files_truncated"] is False


def test_fetch_bundle_truncates_large_diffs_and_flags_it(monkeypatch):
    client = _FakeClient(_metadata(), [[]])
    bundle = _run_bundle(monkeypatch, client, diff_text="x" * (MAX_DIFF_CHARS + 100))
    assert bundle["diff_truncated"] is True
    assert len(bundle["diff_text"]) <= MAX_DIFF_CHARS


def test_fetch_bundle_caps_file_pages_and_flags_truncation(monkeypatch):
    many = [{"filename": f"f{i}.py", "status": "modified"} for i in range(120)]
    client = _FakeClient(_metadata(), [many])
    bundle = _run_bundle(monkeypatch, client, diff_text="x")
    assert len(bundle["files"]) == diff_fetch_module.MAX_PR_FILES
    assert bundle["files_truncated"] is True


def _file_rows(start, count):
    return [
        {"filename": f"f{i}.py", "status": "modified", "additions": 1, "deletions": 0, "patch": "@@ x"}
        for i in range(start, start + count)
    ]


def test_fetch_bundle_flags_truncation_when_a_full_page_lands_exactly_on_the_cap(monkeypatch):
    """The boundary the cap check used to miss entirely.

    The old `while len(files) < MAX_PR_FILES` header ended the loop the moment
    page 1 filled the list, BEFORE anything could set the truncation flag - so
    a 150-file PR returned 100 files with files_truncated=False, and the review
    header said "Files changed: 150" while the walkthrough covered 100 of them.
    """
    cap = diff_fetch_module.MAX_PR_FILES
    client = _FakeClient(
        _metadata(changed_files=150),
        [_file_rows(0, cap), _file_rows(cap, 50)],
    )
    bundle = _run_bundle(monkeypatch, client, diff_text="x")
    assert len(bundle["files"]) == cap
    assert bundle["changed_files"] == 150
    assert bundle["files_truncated"] is True


def test_fetch_bundle_does_not_claim_truncation_for_exactly_the_cap(monkeypatch):
    """The other half of the same boundary: a PR of exactly MAX_PR_FILES files
    is fully covered, and must not be reported as truncated. Costs one extra
    (empty) page request, which is the only way to tell it apart from the
    first 100 of more."""
    cap = diff_fetch_module.MAX_PR_FILES
    client = _FakeClient(_metadata(changed_files=cap), [_file_rows(0, cap), []])
    bundle = _run_bundle(monkeypatch, client, diff_text="x")
    assert len(bundle["files"]) == cap
    assert bundle["files_truncated"] is False


def test_fetch_bundle_flags_truncation_when_rows_were_dropped_as_unusable(monkeypatch):
    """The declared-count half of the flag, independent of the page cap: rows
    _normalize_file_entry rejects still leave the review with fewer files than
    the PR changed, and the flag has to say so."""
    client = _FakeClient(
        _metadata(changed_files=3),
        [[{"filename": "kept.py", "status": "modified"}, {"filename": "   "}, {}]],
    )
    bundle = _run_bundle(monkeypatch, client, diff_text="x")
    assert len(bundle["files"]) == 1
    assert bundle["files_truncated"] is True


def test_fetch_bundle_maps_diff_download_failures_to_display_errors(monkeypatch):
    client = _FakeClient(_metadata(), [[]])
    monkeypatch.setattr(
        diff_fetch_module.requests, "get",
        lambda url, headers=None, timeout=None: _FakeDiffResponse("", status_code=404),
    )
    with pytest.raises(RuntimeError, match="not found"):
        fetch_pr_review_bundle(client, "o", "r", 3)


def test_normalize_file_entry_rejects_empty_paths_and_unknown_status():
    assert _normalize_file_entry({}) == {}
    assert _normalize_file_entry({"filename": "  "}) == {}
    entry = _normalize_file_entry({"filename": "a.py", "status": "weird", "additions": "x"})
    assert entry["status"] == "modified"
    assert entry["additions"] == 0
    renamed = _normalize_file_entry(
        {"filename": "new.py", "previous_filename": "old.py", "status": "renamed"}
    )
    assert renamed["previous_path"] == "old.py"


# =============================================================================
# review_engine.py - walkthrough grouping
# =============================================================================


def _files():
    return [
        {"path": "src/auth/login.py", "additions": 50, "deletions": 5},
        {"path": "src/auth/token.py", "additions": 30, "deletions": 0},
        {"path": "tests/test_login.py", "additions": 200, "deletions": 0},
        {"path": "README.md", "additions": 2, "deletions": 1},
    ]


def test_walkthrough_groups_by_directory_with_tests_last():
    groups = _group_files_for_walkthrough(_files())
    titles = [group["group_title"] for group in groups]
    assert titles[0] == "src"
    assert titles[-1] in {"tests", "README.md", "Repository root"}
    # Highest-churn group first among non-deprioritized ones.
    assert groups[0]["paths"] == ["src/auth/login.py", "src/auth/token.py"]


def test_walkthrough_caps_groups_and_paths():
    files = [{"path": f"dir{i}/f.py", "additions": 1, "deletions": 0} for i in range(20)]
    groups = _group_files_for_walkthrough(files)
    assert len(groups) <= diff_fetch_module.MAX_PR_FILES  # sanity: bounded
    assert len(groups) <= 8
    assert all(len(group["paths"]) <= 12 for group in groups)


# =============================================================================
# review_engine.py - normalization + verdicts
# =============================================================================


def _payload(**overrides):
    base = {
        "repo": "o/r",
        "pr_number": 3,
        "pr_title": "T",
        "changed_files": 1,
        "additions": 5,
        "deletions": 0,
        "files": [{"path": "x.py"}],
        "files_truncated": False,
        "diff_text": "diff --git a/x.py b/x.py\n+x = 1\n",
        "diff_truncated": False,
    }
    base.update(overrides)
    return base


def _parsed(**overrides):
    base = {
        "title": "Looks fine",
        "overview": "Fine.",
        "confidence": "high",
        "walkthrough": [{"group_title": "Core", "paths": ["x.py"], "explanation": "Why."}],
        "review_findings": [],
        "errors_found": [],
        "category_scores": {key: 90 for key in (
            "correctness", "reliability", "security", "maintainability",
            "readability", "testing", "performance", "architecture",
        )},
        "quality_summary": "",
    }
    base.update(overrides)
    return base


def test_normalize_response_derives_strong_verdict_and_ids():
    agent = ReviewLensAgent()
    result = agent._normalize_response(
        _parsed(review_findings=[{
            "severity": "bogus", "category": "x", "path": "x.py", "line": "4",
            "title": "T", "evidence": "E", "impact": "I", "recommendation": "R",
        }]),
        _payload(),
    )
    assert result["verdict"] == "strong"
    assert result["risk_level"] == "low"
    assert result["quality_score"] == 90
    finding = result["review_findings"][0]
    assert finding["severity"] == "medium"  # unknown severity clamps, never crashes
    assert finding["tier"] == SEVERITY_TIERS["medium"] == "yellow"
    assert finding["line"] == 4
    assert finding["id"] == "f1"
    assert result["review_markdown"]


def test_verdict_gates_critical_and_low_scores():
    agent = ReviewLensAgent()
    critical = agent._normalize_response(
        _parsed(errors_found=[{
            "severity": "critical", "kind": "runtime", "path": "x.py", "line": 1,
            "title": "T", "evidence": "E", "fix": "F",
        }]),
        _payload(),
    )
    assert critical["verdict"] == "not_ready"
    assert critical["risk_level"] == "high"
    assert critical["errors_found"][0]["tier"] == "red"
    low_score = agent._normalize_response(
        _parsed(category_scores={}),
        _payload(),
    )
    # Empty scores clamp to the 72 default -> 72 < 78 -> needs_revision.
    assert low_score["quality_score"] == 72
    assert low_score["verdict"] == "needs_revision"


def test_empty_model_groups_fall_back_to_deterministic_grouping():
    agent = ReviewLensAgent()
    result = agent._normalize_response(_parsed(walkthrough=[]), _payload())
    assert result["walkthrough"][0]["paths"] == ["x.py"]


# =============================================================================
# review_engine.py - deterministic fallback heuristics
# =============================================================================


def test_fallback_flags_hardcoded_secret_and_eval():
    agent = ReviewLensAgent()
    result = agent._normalize_response({
        "title": "T", "overview": "O", "confidence": "low",
        "walkthrough": [], "review_findings": [], "errors_found": [],
        "category_scores": {},
        "quality_summary": "",
        **agent._fallback_review(_payload(diff_text=(
            "diff --git a/x.py b/x.py\n"
            "@@ -0,0 +1,3 @@\n"
            "+api_key = \"sk-live-123\"\n"
            "+eval(user_input)\n"
            "+# TODO: remove this\n"
        ))),
    }, _payload())
    error_titles = [error["title"] for error in result["errors_found"]]
    finding_titles = [finding["title"] for finding in result["review_findings"]]
    assert any("secret" in title.lower() for title in error_titles)
    assert any("Dynamic code execution" in title for title in finding_titles)
    assert any("TODO" in title for title in finding_titles)
    assert result["category_scores"]["security"] <= 40


def test_fallback_is_quiet_on_clean_diffs():
    agent = ReviewLensAgent()
    fallback = agent._fallback_review(_payload(diff_text="diff --git a/x.py b/x.py\n+x = 1\n"))
    assert fallback["review_findings"] == []
    assert fallback["errors_found"] == []
    assert len(fallback["walkthrough"]) == 1


# =============================================================================
# review_engine.py - get_response / answer_question
# =============================================================================


def test_get_response_degrades_to_fallback_when_the_model_fails(monkeypatch):
    monkeypatch.setattr(api_provider, "chat", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    agent = ReviewLensAgent()
    result = agent.get_response(_payload(diff_text="diff --git a/x.py b/x.py\n+x = 1\n"))
    assert result["confidence"] == "low"
    assert "pre-screen" in result["overview"]
    assert result["walkthrough"]


def test_get_response_normalizes_a_model_reply(monkeypatch):
    reply = _parsed()
    monkeypatch.setattr(
        api_provider, "chat",
        lambda **kwargs: {"message": {"content": "```json\n" + json.dumps(reply) + "\n```"}},
    )
    agent = ReviewLensAgent()
    result = agent.get_response(_payload())
    assert result["verdict"] == "strong"
    assert result["raw_response"]


def test_answer_question_validates_inputs():
    agent = ReviewLensAgent()
    with pytest.raises(RuntimeError, match="Type a question"):
        agent.answer_question(diff_text="x", question="   ")
    with pytest.raises(RuntimeError, match="Fetch the pull-request diff"):
        agent.answer_question(diff_text="", question="what changed?")


def test_answer_question_returns_model_text(monkeypatch):
    monkeypatch.setattr(
        api_provider, "chat",
        lambda **kwargs: {"message": {"content": "It adds a health check."}},
    )
    agent = ReviewLensAgent()
    assert agent.answer_question(diff_text="diff\n+x", question="what?") == "It adds a health check."
