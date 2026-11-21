"""Review Lens phase-2 approval and Gitlink handoff, using a fake GitHub client."""
import asyncio

from backend.api.intents_code_review_phase2 import register_code_review_phase2_intents
from backend.domain.graph import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState


PATH = "src/worker.py"
OLD_DIFF = (
    "diff --git a/src/worker.py b/src/worker.py\n"
    "--- a/src/worker.py\n+++ b/src/worker.py\n"
    "@@ -1 +1,2 @@\n keep\n+danger()\n"
)
PATCH = "@@ -1 +1,2 @@\n keep\n+danger()\n"


class FakeGitHubClient:
    def __init__(self):
        self.token = "secret-test-token"
        self.sha = "a" * 40
        self.open = True
        self.files = [{"filename": PATH, "status": "modified", "patch": PATCH}]
        self.posts = []
        self.calls = []

    def get_token(self):
        return self.token

    def request(self, url, params=None):
        self.calls.append((url, params))
        if url.endswith("/files"):
            return self.files
        if not self.open:
            raise RuntimeError("GitHub resource not found.")
        return {"state": "open", "head": {"sha": self.sha}}

    def post_pull_review_comment(self, owner, repo, number, *, body, commit_id, path, line):
        self.posts.append({
            "owner": owner, "repo": repo, "number": number, "body": body,
            "commit_id": commit_id, "path": path, "line": line,
        })
        return True


def _harness(monkeypatch, *, token=True):
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "parent", is_user=True)
    node = doc.add_code_review_node(10, 10, parent.id)
    doc.store_code_review_diff(node.id, pr_url="https://github.com/o/r/pull/3", bundle={
        "repo": "o/r", "pr_number": 3, "pr_state": "open", "head_ref": "review-branch",
        "diff_text": OLD_DIFF, "files": [{"path": PATH, "patch": PATCH}],
    })
    doc.complete_code_review_run(
        node.id, title="Check", overview="", confidence="high", walkthrough=[],
        findings=[{
            "id": "f1", "path": PATH, "line": 2, "title": "Unsafe call",
            "severity": "high", "evidence": "danger() has no guard.",
            "impact": "Unexpected execution.", "recommendation": "Add a guard.",
        }],
        errors=[], scores={}, quality_score=60, verdict="needs_revision",
        risk="high", quality_summary="",
    )
    fake = FakeGitHubClient()
    if not token:
        fake.token = ""
    from backend.api import intents_code_review_phase2 as phase2
    monkeypatch.setattr(phase2, "GitHubRestClient", lambda _manager: fake)
    bus = SessionBus("review-lens-phase2")
    notifications = NotificationState()
    bus.register_topic("scene", doc.scene_payload)
    bus.register_topic("notification", notifications.payload)
    dispatcher = type("Dispatcher", (), {"_settings_manager": object()})()
    register_code_review_phase2_intents(bus, doc, notifications, dispatcher)
    return doc, node, bus, fake


def _call(bus, intent, *args):
    return asyncio.run(bus.dispatch_intent("scene", intent, list(args)))


def test_preview_is_exact_and_cannot_post_until_explicit_approval(monkeypatch):
    doc, node, bus, fake = _harness(monkeypatch)
    preview = _call(bus, "previewCodeReviewComment", node.id, "f1")
    assert preview["pr"] == "o/r#3"
    assert (preview["path"], preview["line"]) == (PATH, 2)
    assert preview["body"] == (
        "**[Review Lens · HIGH] Unsafe call**\n\n"
        "danger() has no guard.\n\n**Impact:** Unexpected execution.\n\n"
        "**Suggested fix:** Add a guard."
    )
    assert fake.posts == []
    rejected = _call(bus, "postCodeReviewComment", node.id, "guessed-token")
    assert "error" in rejected
    assert fake.posts == []
    # Wrong approval consumes the pending preview, so even its real token
    # cannot later be replayed.
    assert "error" in _call(bus, "postCodeReviewComment", node.id, preview["previewId"])
    assert fake.posts == []
    assert "secret-test-token" not in str(preview)
    assert doc.nodes[node.id] is node


def test_authorized_post_only_once_and_exact_server_held_payload(monkeypatch):
    _doc, node, bus, fake = _harness(monkeypatch)
    preview = _call(bus, "previewCodeReviewComment", node.id, "f1")
    assert fake.posts == []
    assert _call(bus, "postCodeReviewComment", node.id, preview["previewId"]) == {"posted": True}
    assert fake.posts == [{
        "owner": "o", "repo": "r", "number": 3, "body": preview["body"],
        "commit_id": "a" * 40, "path": PATH, "line": 2,
    }]
    assert "error" in _call(bus, "postCodeReviewComment", node.id, preview["previewId"])
    assert len(fake.posts) == 1


def test_without_gitlink_connection_never_previews_or_posts(monkeypatch):
    _doc, node, bus, fake = _harness(monkeypatch, token=False)
    result = _call(bus, "previewCodeReviewComment", node.id, "f1")
    assert "Connect Gitlink" in result["error"]
    assert fake.calls == []
    assert fake.posts == []


def test_stale_pr_head_is_rejected_after_preview(monkeypatch):
    _doc, node, bus, fake = _harness(monkeypatch)
    preview = _call(bus, "previewCodeReviewComment", node.id, "f1")
    fake.sha = "b" * 40
    result = _call(bus, "postCodeReviewComment", node.id, preview["previewId"])
    assert "changed" in result["error"]
    assert fake.posts == []


def test_missing_pr_or_line_never_posts(monkeypatch):
    _doc, node, bus, fake = _harness(monkeypatch)
    preview = _call(bus, "previewCodeReviewComment", node.id, "f1")
    fake.open = False
    result = _call(bus, "postCodeReviewComment", node.id, preview["previewId"])
    assert "no longer exists" in result["error"]
    assert fake.posts == []
    fake.open = True
    preview = _call(bus, "previewCodeReviewComment", node.id, "f1")
    fake.files = [{"filename": PATH, "status": "modified", "patch": "@@ -1 +1 @@\n keep\n"}]
    result = _call(bus, "postCodeReviewComment", node.id, preview["previewId"])
    assert "no longer exists as an added" in result["error"]
    assert fake.posts == []


def test_re_review_or_dismissal_invalidates_approval(monkeypatch):
    doc, node, bus, fake = _harness(monkeypatch)
    preview = _call(bus, "previewCodeReviewComment", node.id, "f1")
    doc.store_code_review_diff(node.id, pr_url=node.state.code_review_pr_url, bundle={
        "repo": "o/r", "pr_number": 3, "diff_text": OLD_DIFF,
    })
    assert "error" in _call(bus, "postCodeReviewComment", node.id, preview["previewId"])
    assert fake.posts == []


def test_propose_fix_creates_undoable_gitlink_draft_without_any_push(monkeypatch):
    doc, node, bus, fake = _harness(monkeypatch, token=False)
    reply = _call(bus, "proposeCodeReviewFix", node.id, "f1")
    child = doc.nodes[reply["nodeId"]]
    assert child.kind == "gitlink"
    assert child.state.gitlink_repo == "o/r"
    assert child.state.gitlink_branch == "review-branch"
    assert child.state.gitlink_selected_paths == [PATH]
    assert "Line range: 2-2" in child.state.gitlink_task_prompt
    assert "danger() has no guard." in child.state.gitlink_task_prompt
    assert child.state.gitlink_pending_changes == []
    assert fake.posts == []
    assert fake.calls == []
    doc.undo()
    assert reply["nodeId"] not in doc.nodes


def test_unanchored_finding_never_gains_a_post_preview(monkeypatch):
    doc, node, bus, fake = _harness(monkeypatch)
    node.state.code_review_findings[0]["line"] = 99
    response = _call(bus, "previewCodeReviewComment", node.id, "f1")
    assert "added line" in response["error"]
    assert fake.posts == []

def test_gitlink_http_post_is_authenticated_and_not_redirectable(monkeypatch):
    from graphlink_plugins.common.github_client import GitHubRestClient
    import requests

    calls = []
    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return type("Response", (), {"status_code": 201})()

    monkeypatch.setattr(requests, "post", fake_post)
    settings = type("Settings", (), {"get_github_token": lambda self: "secret-test-token"})()
    client = GitHubRestClient(settings)
    assert client.post_pull_review_comment(
        "o", "r", 3, body="exact body", commit_id="a" * 40,
        path=PATH, line=2,
    ) is True
    url, kwargs = calls[0]
    assert url == "https://api.github.com/repos/o/r/pulls/3/comments"
    assert kwargs["headers"]["Authorization"] == "Bearer secret-test-token"
    assert kwargs["json"] == {
        "body": "exact body", "commit_id": "a" * 40,
        "path": PATH, "line": 2, "side": "RIGHT",
    }
    assert "secret-test-token" not in str(kwargs["json"])
    assert kwargs["allow_redirects"] is False


def test_gitlink_http_post_rejects_unconfigured_token_and_stale_line(monkeypatch):
    from graphlink_plugins.common.github_client import GitHubRestClient
    import requests
    import pytest

    calls = []
    def fake_post(url, **kwargs):
        calls.append(url)
        return type("Response", (), {"status_code": 422})()

    monkeypatch.setattr(requests, "post", fake_post)
    settings = type("Settings", (), {"get_github_token": lambda self: ""})()
    client = GitHubRestClient(settings)
    with pytest.raises(RuntimeError, match="Connect Gitlink"):
        client.post_pull_review_comment("o", "r", 3, body="x", commit_id="a"*40, path=PATH, line=2)
    assert calls == []

    settings.get_github_token = lambda: "secret-test-token"
    with pytest.raises(RuntimeError, match="no longer commentable"):
        client.post_pull_review_comment("o", "r", 3, body="x", commit_id="a"*40, path=PATH, line=2)
    assert len(calls) == 1
