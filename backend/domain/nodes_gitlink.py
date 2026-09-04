"""GitlinkOps - every SceneDocument method belonging to the `gitlink` node
kind.

A MIXIN, composed exactly once, by backend/domain/graph.py's SceneDocument.
Method bodies are relocated VERBATIM from graph.py; only the class wrapper
and the imports are new. See backend/domain/nodes_code_review.py's own
docstring for why these per-kind groups are being lifted out.
"""

from __future__ import annotations

from typing import Any

from backend.domain._composed import SceneDocumentParts
from backend.domain.model import SceneError, SceneNode
from backend.domain.node_states import GitlinkState

class GitlinkOps(SceneDocumentParts):
    def add_gitlink_node(self, x: float, y: float, parent_id: str | None) -> SceneNode:
        """The Gitlink node's creation primitive - same required-parent
        posture as document/thinking/html/image/conversation/web_research/
        artifact nodes (never exists unparented - confirmed against
        graphlink_plugin_portal.py's own no_selection_message/
        invalid_parent_message for Gitlink, there is no unparented/root form
        in the domain model). Title is always the fixed literal "Gitlink"
        (mirrors conversation/web_research/artifact's own fixed titles)."""
        if parent_id is not None and parent_id not in self.nodes:
            raise SceneError(f"unknown parent node: {parent_id}")
        node_id = f"n{next(self._counter)}"
        node = SceneNode(
            id=node_id,
            x=float(x),
            y=float(y),
            title="Gitlink",
            kind="gitlink",
            state=GitlinkState(),
        )
        self.nodes[node_id] = node
        if parent_id is not None:
            self.connect(parent_id, node_id)
        return node

    def set_gitlink_local_root(self, node_id: str, local_root: str) -> SceneNode:
        """The one dedicated config setter Gitlink needs (see the design
        rationale on every other config field being passed as a direct
        action parameter instead): the user may type/paste a local checkout
        path BEFORE ever clicking Import/Build Context, with no other action
        call site to piggyback on."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "gitlink":
            raise SceneError(f"node is not a gitlink node: {node_id}")
        node.state.gitlink_local_root = str(local_root)
        return node

    def store_gitlink_repo_tree(self, node_id: str, repo: str, branch: str, file_paths: list[str]) -> SceneNode:
        """Lands a successful loadGitlinkRepoTree result: repo, branch
        (resolved server-side, including any default-branch lookup), and the
        scanned text-file path list."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "gitlink":
            raise SceneError(f"node is not a gitlink node: {node_id}")
        node.state.gitlink_repo = str(repo)
        node.state.gitlink_branch = str(branch)
        node.state.gitlink_repo_file_paths = list(file_paths)
        return node

    def store_gitlink_snapshot_root(self, node_id: str, repo: str, branch: str, local_root: str) -> SceneNode:
        """Lands a successful importGitlinkSnapshot result - sets
        repo/branch/local_root AND gitlink_imported_root (so a later run
        knows this path came from an import, matching legacy repo_state's
        imported_root concept)."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "gitlink":
            raise SceneError(f"node is not a gitlink node: {node_id}")
        node.state.gitlink_repo = str(repo)
        node.state.gitlink_branch = str(branch)
        node.state.gitlink_local_root = str(local_root)
        node.state.gitlink_imported_root = str(local_root)
        return node

    def store_gitlink_context(
        self,
        node_id: str,
        *,
        scope_mode: str,
        selected_paths: list[str],
        context_xml: str,
        context_stats: dict[str, Any],
        context_summary: str,
    ) -> SceneNode:
        """Lands a successful buildGitlinkContext result: scope_mode,
        selected_paths, and all three context_* fields. context_stats is
        stringified value-by-value here - repository.py's
        build_context_bundle returns a mixed int/str dict, but the wire field
        this feeds (scene_payload()'s "gitlinkContextStats") must stay
        honestly dict[str, str] for the codegen'd validator (see the field's
        own comment on SceneNode).

        R5.3 post-review FIX 6: gitlink_context_version is incremented
        UNCONDITIONALLY every time this method runs - a genuine monotonic
        counter, never reset, never skipped - closing a real bug
        gitlink_context_summary alone could not: two different Build Context
        results (e.g. selecting a different single file each time) can
        produce an IDENTICAL summary string (see that field's own comment on
        SceneNode), which was tricking the frontend's lazy-fetch-once guard
        into skipping a real refetch and showing stale XML."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "gitlink":
            raise SceneError(f"node is not a gitlink node: {node_id}")
        node.state.gitlink_scope_mode = str(scope_mode)
        node.state.gitlink_selected_paths = list(selected_paths)
        node.state.gitlink_context_xml = str(context_xml)
        node.state.gitlink_context_stats = {str(k): str(v) for k, v in (context_stats or {}).items()}
        node.state.gitlink_context_summary = str(context_summary)
        node.state.gitlink_context_version += 1
        return node

    def fetch_gitlink_context_xml(self, node_id: str) -> str:
        """The read-side of the lazy fetch: gitlink_context_xml is EXCLUDED
        from scene_payload() (see the field's own comment on SceneNode) - this
        is the only way the frontend ever gets the full text, via the
        read-only fetchGitlinkContext intent."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "gitlink":
            raise SceneError(f"node is not a gitlink node: {node_id}")
        return node.state.gitlink_context_xml

    def start_gitlink_run(self, node_id: str, task_prompt: str) -> SceneNode:
        """Begin one Generate Change Set run: stores the task prompt and
        clears any previous error. Deliberately does NOT touch
        gitlink_pending_changes/gitlink_proposal_markdown/
        gitlink_change_fingerprint here - those only change once
        complete_gitlink_run lands a real result, same stale-while-revalidate
        posture web research's own start_web_research_run documents for
        research_result."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        if node.kind != "gitlink":
            raise SceneError(f"node is not a gitlink node: {node_id}")
        node.state.gitlink_task_prompt = str(task_prompt)
        node.state.gitlink_error = ""
        return node

    def complete_gitlink_run(
        self,
        node_id: str,
        proposal_markdown: str,
        pending_changes: list[dict[str, Any]],
        preview_text: str,
        fingerprint: str | None,
        local_root: str,
    ) -> SceneNode:
        """Land a successful run. proposal_markdown/pending_changes/
        preview_text are always set. If pending_changes is non-empty:
        change_state becomes "previewed", fingerprint is recorded, AND
        (R5.3 post-review FIX 2) gitlink_change_local_root records the
        EXACT local_root this run used - the write-destination binding
        start_gitlink_apply's fourth check enforces, since the fingerprint
        alone says nothing about where the content is written. If
        pending_changes is empty (the agent's own write_intent came back
        no_changes or blocked): change_state becomes "draft" and both
        fingerprint and local_root are cleared - mirrors legacy
        set_proposal's own unconditional `change_state = PREVIEWED if
        pending_changes else DRAFT` exactly (an empty proposal is never
        something to approve), extended so an empty proposal never leaves a
        dangling local_root binding behind either.

        `local_root` is compared as raw trimmed text against
        start_gitlink_apply's own local_root_text - stored stripped here so
        that comparison lines up exactly."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.state.gitlink_proposal_markdown = str(proposal_markdown)
        node.state.gitlink_pending_changes = list(pending_changes or [])
        node.state.gitlink_preview_text = str(preview_text)
        if node.state.gitlink_pending_changes:
            node.state.gitlink_change_state = "previewed"
            node.state.gitlink_change_fingerprint = fingerprint
            node.state.gitlink_change_local_root = str(local_root).strip()
        else:
            node.state.gitlink_change_state = "draft"
            node.state.gitlink_change_fingerprint = None
            node.state.gitlink_change_local_root = None
        return node

    def fail_gitlink_run(self, node_id: str, message: str) -> SceneNode | None:
        """No-op (return None without raising) if the node is gone - a
        background failure landing after node deletion should be silent,
        matching the more defensive posture used for other failure-only
        paths in this file (e.g. apply_web_research_progress). Deliberately
        does NOT clear any existing pending_changes/proposal_markdown/
        change_state - a failed re-run must never wipe out a previously
        staged, still-valid proposal; only the error banner reflects the
        new failure."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        node.state.gitlink_error = str(message)
        return node

    def complete_gitlink_apply(self, node_id: str, written_files: int) -> SceneNode:
        """Land a successful apply: change_state becomes "applied", error is
        cleared.

        R5.3 post-review FIX 1 (CRITICAL): ALSO clears gitlink_pending_changes
        and gitlink_change_fingerprint - a successful Apply must invalidate
        the approval it just consumed, or the exact same already-applied
        change set could be replayed via a second applyGitlinkChanges call
        (start_gitlink_apply's fingerprint check would still pass, since
        nothing here previously changed after a successful write).
        gitlink_change_local_root is cleared alongside them (R5.3 post-review
        FIX 2) - a cleared approval must have no dangling bound fields.
        gitlink_proposal_markdown/gitlink_preview_text are DELIBERATELY left
        untouched - they remain visible as a historical record of what was
        applied."""
        node = self.nodes.get(node_id)
        if node is None:
            raise SceneError(f"unknown node: {node_id}")
        node.state.gitlink_change_state = "applied"
        node.state.gitlink_error = ""
        node.state.gitlink_pending_changes = []
        node.state.gitlink_change_fingerprint = None
        node.state.gitlink_change_local_root = None
        return node

    def fail_gitlink_apply(self, node_id: str, message: str) -> SceneNode | None:
        """No-op if the node is gone. Reverts change_state to "previewed"
        (NEVER silently "applied"), CLEARS gitlink_change_fingerprint (so a
        stale approval can never be replayed) and gitlink_change_local_root
        (R5.3 post-review FIX 2 - a cleared approval must have no dangling
        bound fields), and sets gitlink_error verbatim. Handles BOTH the
        fingerprint-mismatch refusal path, the local_root-mismatch refusal
        path, and the write-failure path identically - all three are "the
        apply did not happen, here is why"."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        node.state.gitlink_change_state = "previewed"
        node.state.gitlink_change_fingerprint = None
        node.state.gitlink_change_local_root = None
        node.state.gitlink_error = str(message)
        return node
