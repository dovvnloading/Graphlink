"""Reviewable Artifact revisions: no model/network access required."""

from __future__ import annotations

import hashlib
import re

import pytest

from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError
from backend.session_load import _artifact_proposal_from_payload, _restore_artifact_payload
from backend.session_save import _serialize_artifact_node


def _document():
    document = SceneDocument()
    node = document.add_artifact_node(0, 0, None)
    node.state.artifact_content = "# Current\n\nKeep me."
    return document, node


def test_generated_candidate_does_not_overwrite_the_saved_document():
    doc, node = _document()
    doc.stage_artifact_proposal(node.id, "# New\n\nChange me.", "Changed the heading.", "# Current\n\nKeep me.")
    assert node.state.artifact_content == "# Current\n\nKeep me."
    assert re.fullmatch(r"[0-9a-f]{32}", node.state.artifact_proposal_id)
    assert node.state.artifact_proposal_base_sha256 == hashlib.sha256(b"# Current\n\nKeep me.").hexdigest()
    fetched = doc.fetch_artifact_proposal(node.id)
    assert fetched == {
        "proposalId": node.state.artifact_proposal_id,
        "content": "# New\n\nChange me.",
        "baseSha256": hashlib.sha256(b"# Current\n\nKeep me.").hexdigest(),
        "summary": "Changed the heading.",
    }
    assert node.history[-1]["content"] == "Proposed revision ready for review."


def test_reviewed_accept_applies_exact_candidate_and_clears_pending_state():
    doc, node = _document()
    doc.stage_artifact_proposal(node.id, "Replacement", "Summary", node.state.artifact_content)
    proposal_id = node.state.artifact_proposal_id
    doc.record_command(
        "acceptArtifactProposal", "user",
        lambda: doc.accept_artifact_proposal(node.id, proposal_id), node_ids=[node.id],
    )
    assert node.state.artifact_content == "Replacement"
    assert node.state.artifact_proposal_id == ""
    assert doc.fetch_artifact_proposal(node.id) is None
    doc.undo()
    restored = doc.nodes[node.id]
    assert restored.state.artifact_content == "# Current\n\nKeep me."
    assert restored.state.artifact_proposal_id == proposal_id


def test_discard_does_not_touch_document_and_can_be_undone():
    doc, node = _document()
    doc.stage_artifact_proposal(node.id, "candidate", "", node.state.artifact_content)
    proposal_id = node.state.artifact_proposal_id
    doc.record_command(
        "discardArtifactProposal", "user",
        lambda: doc.discard_artifact_proposal(node.id, proposal_id), node_ids=[node.id],
    )
    assert node.state.artifact_content == "# Current\n\nKeep me."
    assert node.state.artifact_proposal_id == ""
    doc.undo()
    assert doc.nodes[node.id].state.artifact_proposal_id == proposal_id


def test_accepted_revision_refuses_stale_changed_document_without_erasing_either():
    doc, node = _document()
    doc.stage_artifact_proposal(node.id, "candidate", "Summary", node.state.artifact_content)
    proposal_id = node.state.artifact_proposal_id
    doc.set_artifact_content(node.id, "Human edit", "# Current\n\nKeep me.")
    with pytest.raises(SceneError, match="changed after the proposal"):
        doc.accept_artifact_proposal(node.id, proposal_id)
    assert node.state.artifact_content == "Human edit"
    assert node.state.artifact_proposal_content == "candidate"


def test_wrong_or_stale_review_ids_cannot_accept_or_discard_other_work():
    doc, node = _document()
    doc.stage_artifact_proposal(node.id, "candidate", "", node.state.artifact_content)
    with pytest.raises(SceneError, match="no longer current"):
        doc.accept_artifact_proposal(node.id, "wrong")
    with pytest.raises(SceneError, match="no longer current"):
        doc.discard_artifact_proposal(node.id, "wrong")
    with pytest.raises(SceneError, match="previous"):
        doc.stage_artifact_proposal(node.id, "candidate2", "", node.state.artifact_content)
    assert node.state.artifact_proposal_content == "candidate"


def test_rejects_stale_source_and_oversized_candidate_before_state_mutation(monkeypatch):
    import backend.domain.nodes_agent_runs as ops

    doc, node = _document()
    with pytest.raises(SceneError, match="changed during generation"):
        doc.stage_artifact_proposal(node.id, "proposal", "", "wrong base")
    monkeypatch.setattr(ops, "ARTIFACT_MAX_CHARS", 4)
    with pytest.raises(SceneError, match="document size limit"):
        doc.stage_artifact_proposal(node.id, "12345", "", node.state.artifact_content)
    assert node.state.artifact_proposal_id == ""


def test_pending_proposal_survives_legacy_compatible_save_and_reload():
    doc, node = _document()
    doc.stage_artifact_proposal(node.id, "suggestion", "Summary", node.state.artifact_content)
    payload = _serialize_artifact_node(node)
    restored = _restore_artifact_payload(payload)
    assert restored.state.artifact_content == node.state.artifact_content
    assert restored.state.artifact_proposal_id == node.state.artifact_proposal_id
    assert restored.state.artifact_proposal_content == "suggestion"
    assert restored.state.artifact_proposal_base_sha256 == node.state.artifact_proposal_base_sha256
    assert restored.state.artifact_proposal_summary == "Summary"


def test_corrupt_legacy_import_cannot_inject_unbounded_pending_proposal():
    doc, node = _document()
    doc.stage_artifact_proposal(node.id, "candidate", "", node.state.artifact_content)
    payload = _serialize_artifact_node(node)
    assert _artifact_proposal_from_payload(payload)
    payload["proposal_content"] = "X" * 256_001
    assert _artifact_proposal_from_payload(payload) == {}
    payload["proposal_content"] = "safe"
    payload["proposal_base_sha256"] = "bogus"
    assert _artifact_proposal_from_payload(payload) == {}
    assert _restore_artifact_payload({"content": "old"}).state.artifact_proposal_id == ""
