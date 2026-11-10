"""Independent adversarial probes for the approval/review integration.

The disposable backend probe substitutes only the uvicorn serving loop so a
real autosave tick can run deterministically. The database, review dispatcher,
and file writers delegate to production implementations throughout.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

from graphlink_plugins.gitlink import apply_transaction
from tools.gitlink_safety_demo import ReviewSession, make_repo, snapshot


def test_safety_backend_real_autosave_cannot_touch_a_nonfixture_knowledge_store(tmp_path, monkeypatch):
    """A chats.db override alone does not isolate the knowledge index writer."""
    import graphlink_token_estimator
    import uvicorn
    from backend import knowledge_store
    from tests_e2e import run_safety_backend

    outside = tmp_path / "outside-fixtures" / "knowledge.db"
    monkeypatch.setattr(knowledge_store, "DEFAULT_DB_PATH", outside)
    monkeypatch.setattr(graphlink_token_estimator, "TIKTOKEN_AVAILABLE", False)
    # main is normally a standalone process. Restore its temporary-directory
    # and environment setup after this in-process bootstrap probe.
    monkeypatch.setattr(tempfile, "tempdir", tempfile.gettempdir())
    monkeypatch.setenv("GRAPHLINK_SAFETY_E2E_TOKEN", "disposable-review-probe-token")
    connections = []
    connect = knowledge_store._connect

    def observed_connect(path, *args, **kwargs):
        connections.append(Path(path).resolve())
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(knowledge_store, "_connect", observed_connect)

    async def force_real_autosave(app):
        endpoint = next(route.endpoint for route in app.router.routes
                        if getattr(route, "path", "") == "/api/e2e/prepare/{kind}")
        fixtures = endpoint.__self__
        await fixtures.prepare("gitlink")
        bus, context = fixtures.context()
        try:
            await bus.autosave_guarded_tick()
            assert context.canvas_document.current_chat_id is not None, "positive control: graph actually saved"
            assert connections, "positive control: real knowledge SQLite writer actually ran"
            assert all(path.is_relative_to(fixtures.root.resolve()) for path in connections)
            assert not outside.exists(), "an isolated test must not touch the caller's default knowledge store"
        finally:
            if bus.autosave_task is not None:
                bus.autosave_task.cancel()
                await asyncio.gather(bus.autosave_task, return_exceptions=True)

    def serve_for_probe(app, **_kwargs):
        asyncio.run(force_real_autosave(app))

    monkeypatch.setattr(uvicorn, "run", serve_for_probe)
    with patch.dict(os.environ, os.environ.copy(), clear=True):
        run_safety_backend.main()


def test_pending_review_cannot_follow_a_replaced_target_symlink_outside_checkout(tmp_path):
    checkout = make_repo(tmp_path, "checkout")
    session = ReviewSession(checkout)
    target = checkout / "first.py"
    sentinel = tmp_path / "outside-sentinel.py"
    # Keep the external file's bytes equal to the baseline. Refusal must come
    # from target validation rather than an incidental content-hash mismatch.
    sentinel.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(sentinel)
    except OSError:
        pytest.skip("symlink creation requires OS permission")
    before = snapshot(tmp_path)
    with patch.object(apply_transaction, "atomic_replace_file", wraps=apply_transaction.atomic_replace_file) as writer:
        error = asyncio.run(session.apply(session.fingerprint))
        assert "symlink" in error
        assert writer.call_count == 0
    assert target.is_symlink()
    assert snapshot(tmp_path) == before


def test_concurrent_apply_then_replay_uses_the_real_busy_guard_and_writes_once(tmp_path):
    checkout = make_repo(tmp_path, "checkout")
    session = ReviewSession(checkout)

    async def double_submit():
        request = [session.node.id, session.fingerprint, ["first.py", "new.py"]]
        await asyncio.gather(
            session.bus.dispatch_intent("scene", "applyGitlinkChanges", request),
            session.bus.dispatch_intent("scene", "applyGitlinkChanges", request),
        )
        tasks = [handle.task for handle in session.dispatcher._runs.values()
                 if handle.kind == "gitlink_apply" and handle.task is not None]
        if tasks:
            await asyncio.gather(*tasks)
        assert session.node.state.gitlink_change_state == "applied"

    with patch.object(apply_transaction, "atomic_replace_file", wraps=apply_transaction.atomic_replace_file) as writer:
        asyncio.run(double_submit())
        assert writer.call_count == 2
        applied = snapshot(tmp_path)
        error = asyncio.run(session.apply(session.fingerprint, ["first.py", "new.py"]))
        assert "no approved change set" in error
        assert writer.call_count == 2
    assert snapshot(tmp_path) == applied
    assert (checkout / "first.py").read_bytes() == b"reviewed first\n"
    assert (checkout / "new.py").read_bytes() == b"reviewed new\n"
    assert (checkout / "second.py").read_bytes() == b"original second\n"
