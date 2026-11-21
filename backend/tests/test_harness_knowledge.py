"""Workspace agent knowledge.add: approval, scoped persistence and file boundaries."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.domain.graph import SceneDocument
from backend.harness import tools_knowledge
from backend.harness.loop import HarnessRunContext
from backend.knowledge_store import list_documents
from backend.providers.base import ToolCall
from backend.tools import FS_READ, KNOWLEDGE_READ, KNOWLEDGE_WRITE, ToolRegistry
from backend.tools_knowledge import register_knowledge_tools


@pytest.fixture
def harness_knowledge(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    db_path = tmp_path / "knowledge.db"
    document = SceneDocument()
    document.current_workspace_id = 17
    agent = document.add_harness_node(10, 20, "Index files")
    agent.state.harness_run_id = "run-1"
    registry = ToolRegistry()
    tools_knowledge.register_workspace_tools(registry, None, document, db_path=db_path)
    decisions = []

    async def approve(call):
        decisions.append((call.name, dict(call.arguments)))
        return True

    ctx = HarnessRunContext(
        granted_scopes=frozenset({FS_READ, KNOWLEDGE_WRITE}),
        request_approval=approve,
        harness_workspace_dir=root,
        harness_workspace_id="ws1",
        harness_node_id=agent.id,
        run_id="run-1",
    )

    def add(path, **kwargs):
        return asyncio.run(registry.invoke(
            ToolCall(id="call-1", name="knowledge.add", arguments={"path": path, **kwargs}), ctx,
        ))

    return root, db_path, document, agent, registry, ctx, decisions, add


def test_approved_add_indexes_only_its_workspace_collection_and_deduplicates(harness_knowledge):
    root, db, document, _agent, registry, _ctx, decisions, add = harness_knowledge
    (root / "notes.md").write_text("# Important\nA hedgehog handbook.\n", encoding="utf-8")
    first = add("notes.md", collection_id=9999)  # Malicious model arg is ignored.
    assert not first.is_error, first.content
    outcome = json.loads(first.content)
    assert outcome["was_new"] is True and outcome["chunk_count"] > 0
    assert outcome["source_uri"] == str((root / "notes.md").resolve())
    assert "current workspace" in outcome["destination"]
    second = add("notes.md")
    assert not second.is_error and json.loads(second.content)["was_new"] is False
    assert len(decisions) == 2  # Even the identical call re-prompts.
    assert registry.approval_for("knowledge.add") == "once"
    assert registry.scopes_for("knowledge.add") == frozenset({FS_READ, KNOWLEDGE_WRITE})
    assert not registry.allows_session_grant("knowledge.add")
    docs = list_documents(db)
    assert len(docs) == 1

    reader = ToolRegistry()
    register_knowledge_tools(reader, document=document, db_path=db)
    async def allow(_call):
        raise AssertionError("Read-only search must not prompt")

    response = asyncio.run(reader.invoke(
        ToolCall("s", "knowledge.search", {"query": "hedgehog"}),
        HarnessRunContext(granted_scopes=frozenset({KNOWLEDGE_READ}), request_approval=allow),
    ))
    hits = json.loads(response.content)["results"]
    assert len(hits) == 1 and hits[0]["trust_label"] == "untrusted data"
    assert hits[0]["citation_id"]

    document.current_workspace_id = 18
    other = asyncio.run(reader.invoke(
        ToolCall("s2", "knowledge.search", {"query": "hedgehog"}),
        HarnessRunContext(granted_scopes=frozenset({KNOWLEDGE_READ}), request_approval=allow),
    ))
    assert json.loads(other.content)["results"] == []


def test_denial_and_scope_check_happen_before_persistent_storage(harness_knowledge):
    root, db, _doc, _agent, _registry, ctx, decisions, add = harness_knowledge
    (root / "notes.txt").write_text("Do not ingest.", encoding="utf-8")
    ctx.granted_scopes = frozenset({KNOWLEDGE_READ, FS_READ})
    out = add("notes.txt")
    assert out.is_error and "knowledge.write" in out.content
    assert not decisions and not db.exists()

    ctx.granted_scopes = frozenset({KNOWLEDGE_WRITE, FS_READ})
    async def deny(_call):
        return False
    ctx.request_approval = deny
    out = add("notes.txt")
    assert out.is_error and "denied approval" in out.content
    assert not db.exists()


@pytest.mark.parametrize("path", ["../secret.md", "missing.txt", ".git/config", "subdir"])
def test_invalid_or_private_source_cannot_be_added(harness_knowledge, tmp_path, path):
    root, db, *_rest, add = harness_knowledge
    (tmp_path / "secret.md").write_text("private")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret")
    (root / "subdir").mkdir()
    result = add(path)
    assert result.is_error
    assert not db.exists()


def test_symlink_escape_is_refused(harness_knowledge, tmp_path):
    root, db, *_rest, add = harness_knowledge
    target = tmp_path / "outside.md"
    target.write_text("Do not index this.")
    try:
        (root / "escape.md").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    result = add("escape.md")
    assert result.is_error and "outside" in result.content
    assert not db.exists()


def test_binary_and_oversized_files_are_refused(harness_knowledge):
    root, db, *_rest, add = harness_knowledge
    (root / "pixel.png").write_bytes(bytes([0x89, 80, 78, 71, 0, 0, 0]))
    assert add("pixel.png").is_error
    (root / "huge.txt").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    result = add("huge.txt")
    assert result.is_error and "limit" in result.content
    assert not db.exists()


def test_no_active_run_or_current_workspace_cannot_add(harness_knowledge):
    root, db, document, _agent, _registry, ctx, _decisions, add = harness_knowledge
    (root / "notes.txt").write_text("Do not index")
    ctx.run_id = "other-run"
    assert add("notes.txt").is_error and not db.exists()
    ctx.run_id = "run-1"
    document.current_workspace_id = None
    assert add("notes.txt").is_error and not db.exists()


def test_workspace_switch_during_extract_never_writes_to_new_collection(harness_knowledge, monkeypatch):
    root, db, document, *_rest, add = harness_knowledge
    (root / "notes.md").write_text("Important text.")
    snapshot = tools_knowledge._snapshot_source

    def switched(*args):
        outcome = snapshot(*args)
        document.current_workspace_id = 18
        return outcome

    monkeypatch.setattr(tools_knowledge, "_snapshot_source", switched)
    result = add("notes.md")
    assert result.is_error and "changed" in result.content
    assert not db.exists()


def test_changed_pending_approved_path_is_refused(harness_knowledge):
    root, db, _document, _agent, registry, ctx, _decisions, _add = harness_knowledge
    (root / "a.md").write_text("Harmless")
    (root / "b.md").write_text("Different")
    call = ToolCall("changed", "knowledge.add", {"path": "a.md"})

    async def mutate(_call):
        call.arguments["path"] = "b.md"
        return True

    ctx.request_approval = mutate
    result = asyncio.run(registry.invoke(call, ctx))
    assert result.is_error and "changed" in result.content
    assert not db.exists()
