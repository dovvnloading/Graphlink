"""Review Lens phase 2: immutable, one-use comment preview and Gitlink fix draft.

Both new actions operate on server-held findings, not client-provided body,
path, line, or patch contents. Posting is never queueable/offline-replayed.
"""
from __future__ import annotations

import asyncio
import secrets
import time

from backend.api._shared import make_publish_scene
from backend.domain.node_access import is_node_of
from backend.domain.node_states import CodeReviewState
from graphlink_plugins.common.github_client import GitHubRestClient
from graphlink_plugins.gitlink.review_comments import (
    ReviewCommentError, format_comment, require_gitlink_connection,
    validate_live_anchor, post_approved_comment,
)
from graphlink_plugins.review_lens.evidence import index_visible_diff
from graphlink_plugins.review_lens.pr_url import parse_pr_url

_PREVIEW_LIFETIME_SECONDS = 300
_POST_BUSY = "review-lens-approved-comment"


def _find(node, finding_id):
    if not isinstance(finding_id, str) or finding_id in node.state.code_review_dismissed_ids:
        return None
    for row in [*node.state.code_review_findings, *node.state.code_review_errors]:
        if isinstance(row, dict) and row.get("id") == finding_id:
            return row
    return None


def _location(node, finding):
    path, line = finding.get("path"), finding.get("line")
    if not isinstance(path, str) or not path or type(line) is not int or line <= 0:
        raise ReviewCommentError("This finding needs an added file/line anchor before it can be shared.")
    original = index_visible_diff(node.state.code_review_diff_text).locate(path, line)
    if original is None or original.kind != "added":
        raise ReviewCommentError("This finding is not on an added line in the reviewed diff. Refresh and review again.")
    return path, line, original.source


def _identity(node):
    try:
        owner, name, number = parse_pr_url(node.state.code_review_pr_url)
    except RuntimeError:
        raise ReviewCommentError("The reviewed pull-request URL is invalid. Fetch it again.") from None
    if node.state.code_review_repo != f"{owner}/{name}" or node.state.code_review_pr_number != number:
        raise ReviewCommentError("The pull-request identity changed. Fetch and review it again.")
    return f"{owner}/{name}", number


def _fix_prompt(finding, *, repo: str, number: int) -> str:
    path, start = finding["path"], finding["line"]
    end = finding.get("end_line", finding.get("line_end", start))
    if type(end) is not int or end < start or end > start + 1000:
        end = start
    return (
        f"Review Lens proposed fix for {repo}#{number}\n"
        f"File: {path}\nLine range: {start}-{end}\n"
        f"Finding: {str(finding.get('title') or '')[:200]}\n"
        f"Severity: {str(finding.get('severity') or '')[:30]}\n"
        f"Evidence: {str(finding.get('evidence') or '')[:1600]}\n"
        f"Impact: {str(finding.get('impact') or '')[:800]}\n"
        f"Suggested fix: {str(finding.get('recommendation') or finding.get('fix') or '')[:1600]}\n\n"
        "Prepare a minimal change set for review; do not write or push anything without approval."
    )


def register_code_review_phase2_intents(bus, document, notifications, agent_dispatcher):
    """Register three small, classifiable intents without expanding the 300-line parent registration."""
    publish_scene = make_publish_scene(bus)
    # Per-session, one unguessable, short-lived approval per node. Never persisted
    # in the scene or sent on a websocket except as an opaque preview ID.
    pending: dict[str, dict] = {}

    async def preview_code_review_comment(node_id, finding_id):
        node = document.nodes.get(node_id) if isinstance(node_id, str) else None
        if not is_node_of(node, "code_review", CodeReviewState) or node.pending_request_id:
            return {"error": "Review Lens is busy or the finding's node no longer exists."}
        finding = _find(node, finding_id)
        if finding is None:
            return {"error": "The finding no longer exists. Review the latest result."}
        version = node.state.code_review_diff_version
        frozen_finding = dict(finding)
        try:
            repo, number = _identity(node)
            path, line, source = _location(node, finding)
            body = format_comment(finding)
            client = GitHubRestClient(agent_dispatcher._settings_manager)
            require_gitlink_connection(client)
            head = await asyncio.to_thread(
                validate_live_anchor, client, repo=repo, number=number,
                path=path, line=line, source=source,
            )
            if (document.nodes.get(node_id) is not node or node.pending_request_id
                or node.state.code_review_diff_version != version
                or _find(node, finding_id) != frozen_finding):
                return {"error": "The review changed during preview. Review the finding again."}
            preview_id = secrets.token_urlsafe(24)
            pending[node_id] = {
                "id": preview_id, "node": node, "version": version, "finding_id": finding_id,
                "finding": frozen_finding, "created": time.monotonic(),
                "repo": repo, "number": number, "path": path, "line": line,
                "source": source, "body": body, "head": head,
            }
            return {
                "previewId": preview_id, "body": body, "path": path, "line": line,
                "pr": f"{repo}#{number}",
            }
        except ReviewCommentError as exc:
            return {"error": str(exc)}
        except Exception:
            return {"error": "Could not preview the PR comment. Check the Gitlink connection and try again."}

    async def post_code_review_comment(node_id, preview_id):
        # Atomically consume authorization BEFORE the first await or network
        # call. A duplicate click, reconnect, or stale second invocation
        # cannot re-use it, regardless of GitHub's response.
        draft = pending.pop(node_id, None) if isinstance(node_id, str) else None
        node = document.nodes.get(node_id) if isinstance(node_id, str) else None
        if (not isinstance(preview_id, str) or draft is None or draft["id"] != preview_id
            or time.monotonic() - draft["created"] > _PREVIEW_LIFETIME_SECONDS):
            return {"error": "The comment preview expired or was already used. Preview again."}
        if (not is_node_of(node, "code_review", CodeReviewState) or node is not draft["node"]
            or node.pending_request_id or node.state.code_review_diff_version != draft["version"]
            or _find(node, draft["finding_id"]) != draft["finding"]):
            return {"error": "The review changed since approval. Preview the finding again."}
        node.pending_request_id = _POST_BUSY  # no await between guard and claim
        try:
            await publish_scene()
            client = GitHubRestClient(agent_dispatcher._settings_manager)
            await asyncio.to_thread(post_approved_comment, client, draft)
            notifications.show("Review comment posted to GitHub.", "info")
            await bus.publish("notification")
            return {"posted": True}
        except ReviewCommentError as exc:
            return {"error": str(exc)}
        except Exception:
            return {"error": "GitHub could not post the review comment. Preview again before retrying."}
        finally:
            if node.pending_request_id == _POST_BUSY:
                node.pending_request_id = None
            await publish_scene()

    async def propose_code_review_fix(node_id, finding_id):
        node = document.nodes.get(node_id) if isinstance(node_id, str) else None
        if not is_node_of(node, "code_review", CodeReviewState) or node.pending_request_id:
            return {"error": "Review Lens is busy or this node no longer exists."}
        finding = _find(node, finding_id)
        if finding is None:
            return {"error": "The finding no longer exists."}
        try:
            repo, number = _identity(node)
            path, _line, _source = _location(node, finding)
            prompt = _fix_prompt(finding, repo=repo, number=number)
        except ReviewCommentError as exc:
            return {"error": str(exc)}

        def create_draft():
            child = document.add_gitlink_node(*document.place_child(node_id, "gitlink"), node_id)
            child.state.gitlink_repo = repo
            child.state.gitlink_branch = node.state.code_review_head_ref
            child.state.gitlink_selected_paths = [path]
            child.state.gitlink_task_prompt = prompt
            return child

        child, _command = document.record_command(
            "proposeCodeReviewFix", "user", create_draft, node_ids=[node_id],
        )
        await publish_scene()
        return {"nodeId": child.id}

    bus.register_intent("scene", "previewCodeReviewComment", preview_code_review_comment)
    bus.register_intent("scene", "postCodeReviewComment", post_code_review_comment)
    bus.register_intent("scene", "proposeCodeReviewFix", propose_code_review_fix)
