"""CodeReviewOps - every SceneDocument method belonging to the Review Lens
(`code_review`) node kind.

A MIXIN, composed exactly once, by backend/domain/graph.py's
`class SceneDocument(BranchOps, GroupOps, LayoutOps, CommandOps, CodeReviewOps, GitlinkOps)`.
Method bodies are relocated VERBATIM from graph.py; only the class wrapper
and the imports are new.

WHY: SceneDocument had 144 public members, 79 of them specific to one node
kind, in a 3,037-line module. Adding a node kind meant editing that class,
which is most of what makes a new kind a 44-file change. The package already
decomposes the CROSS-CUTTING concerns this way (BranchOps, GroupOps,
LayoutOps, CommandOps); this extends the same idiom to the PER-KIND ones,
starting with the two largest groups.

The mixin needs only what SceneDocumentParts already declares - `nodes`,
`connect`, `_counter` - so it inherits that and stays type-checkable in
isolation, exactly like its cross-cutting siblings.
"""

from __future__ import annotations

from backend.domain._composed import SceneDocumentParts
from backend.domain.model import SceneError, SceneNode
from backend.domain.node_access import optional_node, require_node
from backend.domain.node_states import CodeReviewState

def _bundle_int(value: object) -> int:
    """Non-negative int from a fetch bundle, defaulting to 0.

    The bundle is assembled from a GitHub API response, so every numeric
    field in it is external input. graphlink_plugins/review_lens/diff_fetch.py
    already coerces on the way in; this is the second line of the same
    defence, for a bundle that reached here by any other route (a test, a
    future caller, a hand-built payload)."""
    try:
        return max(0, int(value))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


class CodeReviewOps(SceneDocumentParts):
    def add_code_review_node(self, x: float, y: float, parent_id: str | None) -> SceneNode:
        """The Review Lens node's creation primitive - same required-parent
        posture as gitlink (the picker offers standalone creation at the
        viewport center, but the parent edge is attached whenever a valid
        parent was selected). Title is always the fixed literal
        "Review Lens" (mirrors gitlink's own fixed title)."""
        if parent_id is not None and parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title="Review Lens",
            kind="code_review",
            state=CodeReviewState(),
        )
        self.nodes[node_id] = node
        if parent_id is not None:
            self.connect(parent_id, node_id)
        return node

    def set_code_review_pr_url(self, node_id: str, pr_url: str) -> SceneNode:
        """The one dedicated config setter Review Lens needs: the user types
        or pastes the PR link BEFORE ever clicking Fetch, with no other
        action call site to piggyback on (the setGitlinkLocalRoot
        precedent exactly)."""
        node = require_node(self.nodes, node_id, "code_review", CodeReviewState)
        node.state.code_review_pr_url = str(pr_url)
        return node

    def store_code_review_diff(
        self, node_id: str, *, pr_url: str, bundle: dict,
    ) -> SceneNode:
        """Lands a successful fetchCodeReviewDiff result.

        Takes the fetch bundle whole rather than as 15 separate keyword
        arguments. It used to be the latter, which made this the widest
        signature in the repo at 18 parameters - and every one of them was
        pure transport: the caller pulled 15 values out of a dict with
        .get() only for this method to set them straight onto node.state.
        `pr_url` stays separate because it is not part of the bundle: it is
        what the user typed, kept even when the fetch that used it is
        superseded.

        A new fetch
        supersedes any prior review on this node (walkthrough, findings,
        errors, verdict, Q&A, and dismissals are all reset) - reviewing
        against a stale diff's findings would be worse than showing none,
        the same supersede reasoning store_gitlink_context applies to
        context builds. code_review_diff_version is incremented
        UNCONDITIONALLY (the R5.3 post-review FIX 6 precedent) so the
        frontend's lazy-diff guard can never serve a previous fetch's
        text for this one."""
        node = require_node(self.nodes, node_id, "code_review", CodeReviewState)
        pr_number_value = _bundle_int(bundle.get("pr_number"))
        node.state.code_review_pr_url = str(pr_url)
        node.state.code_review_repo = str(bundle.get("repo", ""))
        node.state.code_review_pr_number = pr_number_value
        node.state.code_review_pr_title = str(bundle.get("pr_title", ""))
        node.state.code_review_pr_state = str(bundle.get("pr_state", ""))
        node.state.code_review_pr_html_url = str(bundle.get("html_url", ""))
        node.state.code_review_base_ref = str(bundle.get("base_ref", ""))
        node.state.code_review_head_ref = str(bundle.get("head_ref", ""))
        node.state.code_review_additions = _bundle_int(bundle.get("additions"))
        node.state.code_review_deletions = _bundle_int(bundle.get("deletions"))
        node.state.code_review_changed_files = _bundle_int(bundle.get("changed_files"))
        node.state.code_review_files = [
            dict(entry) for entry in (bundle.get("files") or []) if isinstance(entry, dict)
        ]
        node.state.code_review_files_truncated = bool(bundle.get("files_truncated", False))
        node.state.code_review_diff_text = str(bundle.get("diff_text", ""))
        node.state.code_review_diff_truncated = bool(bundle.get("diff_truncated", False))
        node.state.code_review_diff_chars = _bundle_int(bundle.get("diff_chars"))
        node.state.code_review_diff_version += 1
        node.state.code_review_walkthrough = []
        node.state.code_review_findings = []
        node.state.code_review_errors = []
        node.state.code_review_dismissed_ids = []
        node.state.code_review_title = ""
        node.state.code_review_overview = ""
        node.state.code_review_confidence = ""
        node.state.code_review_scores = {}
        node.state.code_review_quality_score = 0
        node.state.code_review_verdict = "none"
        node.state.code_review_risk = ""
        node.state.code_review_quality_summary = ""
        node.state.code_review_qa = []
        node.state.code_review_state = "fetched"
        node.state.code_review_error = ""
        return node

    def fetch_code_review_diff_text(self, node_id: str) -> str:
        """The read-side of the lazy fetch: code_review_diff_text is
        EXCLUDED from scene_payload() (see CodeReviewState's own comment) -
        this is the only way the frontend ever gets the full text, via the
        read-only fetchCodeReviewDiffText intent."""
        node = require_node(self.nodes, node_id, "code_review", CodeReviewState)
        return node.state.code_review_diff_text

    def start_code_review_run(self, node_id: str) -> SceneNode:
        """Mark a review run started: clears the error banner but keeps any
        prior review visible until the new one lands (stale-while-
        revalidate, the start_gitlink_run precedent - a failed re-run must
        never blank a previously good review)."""
        node = require_node(self.nodes, node_id, "code_review", CodeReviewState)
        node.state.code_review_error = ""
        return node

    def complete_code_review_run(
        self,
        node_id: str,
        *,
        title: str,
        overview: str,
        confidence: str,
        walkthrough: list,
        findings: list,
        errors: list,
        scores: dict,
        quality_score: int,
        verdict: str,
        risk: str,
        quality_summary: str,
    ) -> SceneNode:
        """Lands a successful runCodeReview result: the walkthrough,
        findings, errors, and scorecard, plus verdict/risk. Caps are
        re-enforced here (defense in depth - the engine already caps, but
        the domain is what bounds the wire and the save file). A new
        review resets dismissals: finding ids are re-minted per review,
        so a dismissal of the old review's f3 must never hide the new
        review's f3."""
        node = require_node(self.nodes, node_id, "code_review", CodeReviewState)
        node.state.code_review_title = str(title)
        node.state.code_review_overview = str(overview)
        node.state.code_review_confidence = str(confidence)
        node.state.code_review_walkthrough = [
            dict(group) for group in (walkthrough or []) if isinstance(group, dict)
        ][:8]
        node.state.code_review_findings = [
            dict(item) for item in (findings or []) if isinstance(item, dict)
        ][:12]
        node.state.code_review_errors = [
            dict(item) for item in (errors or []) if isinstance(item, dict)
        ][:10]
        node.state.code_review_dismissed_ids = []
        node.state.code_review_scores = {
            str(key): max(0, int(value)) for key, value in (scores or {}).items()
        }
        node.state.code_review_quality_score = max(0, int(quality_score or 0))
        node.state.code_review_verdict = str(verdict or "none")
        node.state.code_review_risk = str(risk or "")
        node.state.code_review_quality_summary = str(quality_summary)
        node.state.code_review_state = "reviewed"
        node.state.code_review_error = ""
        return node

    def fail_code_review_run(self, node_id: str, message: str) -> SceneNode | None:
        """No-op (return None without raising) if the node is gone - a
        background failure landing after node deletion should be silent
        (the fail_gitlink_run precedent). Deliberately does NOT clear any
        prior review - a failed re-run must never wipe out a previously
        good one; only the error banner reflects the new failure."""
        node = optional_node(self.nodes, node_id, "code_review", CodeReviewState)
        if node is None:
            return None
        node.state.code_review_error = str(message)
        return node

    def dismiss_code_review_finding(self, node_id: str, finding_id: str) -> SceneNode:
        """Record one finding/error dismissal (the reviewer's dismiss
        affordance). Idempotent: unknown ids and repeats are quiet no-ops,
        never errors - dismissal is UI state, and a double-click must not
        be able to fail a run."""
        node = require_node(self.nodes, node_id, "code_review", CodeReviewState)
        dismissed = str(finding_id)
        known_ids = {
            str(item.get("id")) for item in (
                list(node.state.code_review_findings) + list(node.state.code_review_errors)
            ) if isinstance(item, dict)
        }
        if dismissed and dismissed in known_ids and dismissed not in node.state.code_review_dismissed_ids:
            node.state.code_review_dismissed_ids.append(dismissed)
        return node

    def append_code_review_qa(self, node_id: str, question: str, answer: str) -> SceneNode:
        """Land one answered follow-up. Capped at the 20 most recent
        entries - the Q&A list is on the wire (unlike the diff text), so
        unbounded growth here would be unbounded wire growth."""
        node = require_node(self.nodes, node_id, "code_review", CodeReviewState)
        node.state.code_review_qa.append({
            "question": str(question),
            "answer": str(answer),
        })
        node.state.code_review_qa = node.state.code_review_qa[-20:]
        return node
