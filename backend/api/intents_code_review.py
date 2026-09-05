"""Review Lens node - PR-diff fetch, review run, follow-up Q&A, finding
dismissal.

Mirrors backend/api/intents_gitlink.py's own structure (busy pre-checks,
the synchronous placeholder-claim race fix via claim_busy_node_or_notify,
dispatcher handoff with on_success/on_failure callbacks landing into
SceneDocument store/complete/fail methods), applied to Review Lens's own
fetch -> review -> discuss flow. The review run reuses the existing
generic pending_request_id field as the busy/in-flight marker for every
Review Lens action on a node (fetch, run, ask) - exactly that field's
documented purpose.

Undo posture (see tests/undo_classification.py): setCodeReviewPrUrl and
dismissCodeReviewFinding wrap their mutation in record_command (A);
fetch/run/ask/cancel are run-lifecycle caching (B), the same split
intents_gitlink.py already establishes.
"""

from __future__ import annotations

from backend.agents import _NODE_RUN_CLAIM_PLACEHOLDER, AgentDispatcher
from backend.api._shared import claim_busy_node_or_notify, make_publish_scene
from backend.domain.graph import SceneDocument
from backend.domain.node_access import is_node_of
from backend.domain.node_states import CodeReviewState
from backend.events import SessionBus
from backend.notifications import NotificationState


def register_code_review_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher: AgentDispatcher,
) -> None:
    publish_scene = make_publish_scene(bus)

    async def set_code_review_pr_url(node_id, pr_url):
        document.record_command(
            "setCodeReviewPrUrl", "user",
            lambda: document.set_code_review_pr_url(node_id, pr_url),
            node_ids=[node_id],
        )
        await publish_scene()

    async def fetch_code_review_diff(node_id, pr_url=None):
        node = document.nodes.get(node_id)
        if not is_node_of(node, "code_review", CodeReviewState):
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        if node.pending_request_id:
            notifications.show("Review Lens is busy for this node.", "info")
            await bus.publish("notification")
            return None
        effective_url = (pr_url or "").strip() or node.state.code_review_pr_url
        if not effective_url.strip():
            notifications.show("Paste a pull-request URL first.", "warning")
            await bus.publish("notification")
            return None
        bundle = await agent_dispatcher.fetch_code_review_diff(
            bus=bus, notifications_state=notifications, node=node, pr_url=effective_url,
        )
        if bundle is not None:
            document.store_code_review_diff(node_id, pr_url=effective_url, bundle=bundle)
            await publish_scene()
        return node_id

    async def fetch_code_review_diff_text(node_id):
        return document.fetch_code_review_diff_text(node_id)

    async def run_code_review(node_id):
        node = document.nodes.get(node_id)
        if not is_node_of(node, "code_review", CodeReviewState):
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        if not (node.state.code_review_diff_text or "").strip():
            notifications.show("Fetch the pull-request diff before running a review.", "warning")
            await bus.publish("notification")
            return None
        # The busy pre-check, synchronous placeholder claim, and SceneError
        # recovery are shared with Gitlink's runGitlinkChangeSet - see
        # claim_busy_node_or_notify's own docstring for exactly why the
        # claim must land in the same synchronous stretch as the pre-check.
        node = await claim_busy_node_or_notify(
            bus, document, notifications, node_id,
            busy_message="Review Lens is already busy for this node.",
            placeholder=_NODE_RUN_CLAIM_PLACEHOLDER,
            start_run=lambda: document.start_code_review_run(node_id),
        )
        if node is None:
            return None
        await publish_scene()

        # Frozen at dispatch time: the run reviews the diff as fetched,
        # never whatever a concurrent fetch lands mid-run.
        bundle = {
            "repo": node.state.code_review_repo,
            "pr_number": node.state.code_review_pr_number,
            "pr_title": node.state.code_review_pr_title,
            "changed_files": node.state.code_review_changed_files,
            "additions": node.state.code_review_additions,
            "deletions": node.state.code_review_deletions,
            "files": [dict(entry) for entry in node.state.code_review_files],
            "files_truncated": node.state.code_review_files_truncated,
            "diff_text": node.state.code_review_diff_text,
            "diff_truncated": node.state.code_review_diff_truncated,
        }

        def _on_success(result):
            document.complete_code_review_run(
                node_id,
                title=result.get("title", ""),
                overview=result.get("overview", ""),
                confidence=result.get("confidence", ""),
                walkthrough=result.get("walkthrough", []),
                findings=result.get("review_findings", []),
                errors=result.get("errors_found", []),
                scores=result.get("category_scores", {}),
                quality_score=result.get("quality_score", 0),
                verdict=result.get("verdict", "none"),
                risk=result.get("risk_level", ""),
                quality_summary=result.get("quality_summary", ""),
            )

        def _on_failure(message):
            document.fail_code_review_run(node_id, message)

        await agent_dispatcher.start_code_review_run(
            bus=bus, notifications_state=notifications, node=node, node_id=node_id,
            bundle=bundle, on_success=_on_success, on_failure=_on_failure,
        )
        return node_id

    async def cancel_code_review_request(request_id):
        # The return value is load-bearing, not decoration. Only a review RUN
        # is registered as a cancellable run; a diff fetch and an Ask claim
        # the same node.pending_request_id busy marker through
        # _run_node_blocking_action, which owns no cancellation primitive.
        # The node offers Cancel for any pending request, so dropping this
        # False left the user clicking a button that did nothing and said
        # nothing. It cannot be hidden at the UI layer either - the wire
        # carries one opaque pendingRequestId with no kind attached.
        if not agent_dispatcher.cancel_code_review(request_id):
            notifications.show(
                "Only a running review can be cancelled. The pull-request "
                "fetch will finish on its own.",
                "info",
            )
            await bus.publish("notification")

    async def ask_code_review_question(node_id, question):
        node = document.nodes.get(node_id)
        if not is_node_of(node, "code_review", CodeReviewState):
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        if node.pending_request_id:
            notifications.show("Review Lens is busy for this node.", "info")
            await bus.publish("notification")
            return None
        if not (node.state.code_review_diff_text or "").strip():
            notifications.show("Fetch the pull-request diff before asking about it.", "warning")
            await bus.publish("notification")
            return None
        answer = await agent_dispatcher.ask_code_review_question(
            bus=bus, notifications_state=notifications, node=node,
            question=question, review_summary=node.state.code_review_quality_summary,
        )
        if answer is not None:
            document.append_code_review_qa(node_id, question, answer)
            await publish_scene()
        return node_id

    async def dismiss_code_review_finding(node_id, finding_id):
        document.record_command(
            "dismissCodeReviewFinding", "user",
            lambda: document.dismiss_code_review_finding(node_id, finding_id),
            node_ids=[node_id],
        )
        await publish_scene()

    bus.register_intent("scene", "setCodeReviewPrUrl", set_code_review_pr_url)
    bus.register_intent("scene", "fetchCodeReviewDiff", fetch_code_review_diff)
    bus.register_intent("scene", "fetchCodeReviewDiffText", fetch_code_review_diff_text)
    bus.register_intent("scene", "runCodeReview", run_code_review)
    bus.register_intent("scene", "cancelCodeReviewRequest", cancel_code_review_request)
    bus.register_intent("scene", "askCodeReviewQuestion", ask_code_review_question)
    bus.register_intent("scene", "dismissCodeReviewFinding", dismiss_code_review_finding)
