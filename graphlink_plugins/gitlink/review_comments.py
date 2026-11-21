"""Gitlink's authenticated, review-only GitHub inline comment operations.

Review Lens owns the finding and the human approval; Gitlink owns GitHub
access. Both preview and submission validate the *current* PR and added
new-side line. Only the server-held, approved body is sent to GitHub.
"""
from __future__ import annotations

from graphlink_plugins.review_lens.evidence import index_visible_diff

_MAX_PR_PAGES = 30  # GitHub caps a PR's changed files at 3,000.


class ReviewCommentError(RuntimeError):
    """Safe, human-readable error; never contains API responses or credentials."""


def require_gitlink_connection(client) -> None:
    try:
        token = client.get_token()
    except Exception:
        token = ""
    if not isinstance(token, str) or not token.strip():
        raise ReviewCommentError("Connect Gitlink to GitHub in Settings > Integrations before posting comments.")


def _get(client, url: str, *, params=None):
    try:
        return client.request(url, params=params)
    except Exception as exc:
        # GitHubRestClient's 404 is known, but generic API error bodies are
        # uncontrolled. Never echo those bodies (or a credential) to the wire.
        if "not found" in str(exc).lower() or "404" in str(exc):
            raise ReviewCommentError("The pull request no longer exists or is not accessible. Refresh Review Lens.") from None
        if "token" in str(exc).lower():
            raise ReviewCommentError("Gitlink's GitHub connection was rejected. Check Settings > Integrations.") from None
        raise ReviewCommentError("Could not verify the pull request with GitHub. Try again.") from None


def validate_live_anchor(
    client, *, repo: str, number: int, path: str, line: int, source: str,
    expected_head: str | None = None,
) -> str:
    """Return current head SHA iff the requested added line still exists.

    A claimed file/line from model output is never enough. Both the original
    Review Lens diff and current GitHub /files patch must anchor the claim.
    """
    require_gitlink_connection(client)
    if not isinstance(repo, str) or len(repo.split("/")) != 2:
        raise ReviewCommentError("The reviewed pull request is missing. Fetch it again.")
    owner, name = repo.split("/")
    from graphlink_plugins.review_lens.pr_url import _SAFE_SEGMENT
    if any(not part or part in {".", ".."} or not _SAFE_SEGMENT.fullmatch(part) for part in (owner, name)):
        raise ReviewCommentError("The reviewed repository is invalid. Fetch the pull request again.")
    if type(number) is not int or number <= 0 or not isinstance(path, str) or not path or type(line) is not int or line <= 0:
        raise ReviewCommentError("This finding has no commentable file and added line.")
    api = f"https://api.github.com/repos/{owner}/{name}/pulls/{number}"
    metadata = _get(client, api)
    if not isinstance(metadata, dict):
        raise ReviewCommentError("GitHub did not return a valid pull request. Refresh Review Lens.")
    if metadata.get("state") != "open":
        raise ReviewCommentError("The pull request is no longer open. Refresh Review Lens.")
    head = metadata.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(head_sha, str) or not head_sha:
        raise ReviewCommentError("GitHub did not return the PR head revision. Refresh Review Lens.")
    if expected_head is not None and head_sha != expected_head:
        raise ReviewCommentError("The pull request changed since the preview. Refresh Review Lens before posting.")

    entry = None
    for page in range(1, _MAX_PR_PAGES + 1):
        rows = _get(client, api + "/files", params={"page": page, "per_page": 100})
        if not isinstance(rows, list):
            raise ReviewCommentError("GitHub did not return a valid changed-file list.")
        entry = next((item for item in rows if isinstance(item, dict) and item.get("filename") == path), None)
        if entry is not None or len(rows) < 100:
            break
    if entry is None or entry.get("status") == "removed":
        raise ReviewCommentError("This file is no longer part of the pull-request diff. Refresh Review Lens.")
    patch = entry.get("patch")
    if not isinstance(patch, str) or not patch:
        raise ReviewCommentError("The selected line is no longer in a commentable file patch. Refresh Review Lens.")
    live = index_visible_diff(f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n{patch}").locate(path, line)
    if live is None or live.kind != "added":
        raise ReviewCommentError("The selected line no longer exists as an added PR line. Refresh Review Lens.")
    if live.source != source:
        raise ReviewCommentError("The code at this line changed since the review. Refresh Review Lens.")
    return head_sha


def format_comment(finding: dict) -> str:
    """The exact Markdown shown to the reviewer and later posted unchanged."""
    title = str(finding.get("title") or "Review Lens finding")[:200]
    severity = str(finding.get("severity") or "review")[:30].upper()
    evidence = str(finding.get("evidence") or "").strip()[:1600]
    recommendation = str(finding.get("recommendation") or finding.get("fix") or "").strip()[:1600]
    impact = str(finding.get("impact") or "").strip()[:800]
    if not evidence or not recommendation:
        raise ReviewCommentError("This finding has no complete evidence and recommendation to post.")
    parts = [f"**[Review Lens · {severity}] {title}**", evidence]
    if impact:
        parts.append(f"**Impact:** {impact}")
    parts.append(f"**Suggested fix:** {recommendation}")
    return "\n\n".join(parts)


def post_approved_comment(client, approved: dict) -> None:
    """Repeat validation immediately before the ONLY GitHub POST."""
    repo = approved["repo"]
    number = approved["number"]
    validate_live_anchor(
        client, repo=repo, number=number, path=approved["path"], line=approved["line"],
        source=approved["source"], expected_head=approved["head"],
    )
    owner, name = repo.split("/")
    try:
        client.post_pull_review_comment(
            owner, name, number, body=approved["body"], commit_id=approved["head"],
            path=approved["path"], line=approved["line"],
        )
    except Exception as exc:
        # Do not echo a fake/injected client or upstream exception message:
        # even one mentioning "token" might contain an actual credential.
        message = str(exc).lower()
        if "token was rejected" in message:
            reason = "Gitlink's GitHub token was rejected. Update it in Settings > Integrations."
        elif "lacks permission" in message:
            reason = "Gitlink's GitHub connection cannot comment on this pull request."
        elif "no longer exists" in message or "not accessible" in message:
            reason = "The pull request no longer exists or is not accessible. Refresh Review Lens."
        elif "no longer commentable" in message:
            reason = "The selected pull-request line is no longer commentable. Refresh the diff."
        elif "connect gitlink" in message:
            reason = "Connect Gitlink to GitHub in Settings > Integrations before posting comments."
        else:
            reason = "GitHub could not post the review comment. Preview again before retrying."
        raise ReviewCommentError(reason) from None
