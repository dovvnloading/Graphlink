"""Preview and cooperatively execute ordinary chat-reply reruns.

Batch progress lives in this session's intent closure, not in the saved scene
wire. The already-versioned chat state is the only persistent mutation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid

from graphlink_token_estimator import TokenEstimator

from backend.api.intents_chat import (
    _history_with_chat_knowledge,
    _latest_user_query,
    _make_on_usage,
    _snapshot_active_provider_model,
    _stamp_reply_provenance,
    _use_knowledge_for_node,
)
from backend.context_engine.citations import apply_reply_citations
from backend.domain.commands import _merge_commands
from backend.domain.model import SceneError
from backend.domain.node_states import ChatState
from backend.domain.staleness import _message_input, fingerprint_for_reply, scene_staleness
from backend.domain.structure import primary_parent_edges

_logger = logging.getLogger(__name__)
_SCOPES = ("this_path", "all_branches_below")


def _rerun_ids(document, root_id: str, scope: str) -> list[str]:
    """Canonical parent-before-child traversal; never follow reference edges."""
    root = document.nodes.get(root_id)
    if root is None or root.kind != "chat" or not isinstance(root.state, ChatState):
        raise SceneError("Re-run from here requires a chat message.")
    if scope not in _SCOPES:
        raise SceneError("Invalid re-run scope.")
    stale = scene_staleness(document)
    if not (
        (root.state.is_user and len(root.state.versions) > 1)
        or (not root.state.is_user and stale.get(root_id) == "out_of_date")
    ):
        raise SceneError("Re-run from here requires an edited user message or an out-of-date reply.")

    chosen = primary_parent_edges(document.nodes, document.edges, set(document.nodes))
    children: dict[str, list[str]] = {}
    for child_id, edge in chosen.items():
        children.setdefault(edge.source, []).append(child_id)

    # The active conversation tip chooses the path when it descends from the
    # clicked message. Otherwise the first canonical structural child wins.
    active_path: set[str] = set()
    cursor = document.last_chat_node_id
    while cursor in document.nodes and cursor not in active_path:
        active_path.add(cursor)
        if cursor == root_id:
            break
        edge = chosen.get(cursor)
        if edge is None:
            break
        cursor = edge.source
    if root_id not in active_path:
        active_path.clear()

    ordered: list[str] = []
    stack = [root_id]
    visited: set[str] = set()
    while stack:
        item = stack.pop()
        if item in visited:
            continue
        visited.add(item)
        node = document.nodes.get(item)
        if node is None:
            continue
        if (
            node.kind == "chat" and isinstance(node.state, ChatState)
            and not node.state.is_user and not node.state.is_branch_synthesis
        ):
            ordered.append(item)
        descendants = children.get(item, ())
        if scope == "this_path":
            next_id = next((child for child in descendants if child in active_path), None)
            if next_id is None:
                next_id = next(
                    (child for child in descendants if document.nodes[child].kind == "chat"),
                    next(iter(descendants), None),
                )
            if next_id is not None:
                stack.append(next_id)
        else:
            stack.extend(reversed(descendants))
    return ordered


def _preview_plan(document, dispatcher, bus, root_id: str, scope: str):
    """The same server-owned selection is recomputed at execution time."""
    from backend.canvas import _history_image_tokens, _history_token_text

    ids = _rerun_ids(document, root_id, scope)
    rows = []
    root = document.nodes[root_id]
    proof = [root_id, scope, root.content, root.state.version_index, len(root.state.versions)]
    estimator = TokenEstimator()
    for node_id in ids:
        node = document.nodes[node_id]
        parent_edge = document._branch_parent_edge(node_id)
        if parent_edge is None:
            raise SceneError("A re-run reply no longer has a structural parent.")
        parent_id = parent_edge.source
        history = document.chat_branch_history(parent_id)
        provider, model = _snapshot_active_provider_model(dispatcher, document, parent_id, bus)
        provider = provider or node.state.provider or ""
        model = model or node.state.model or ""
        estimate = estimator.count_tokens(_history_token_text(history)) + _history_image_tokens(history)
        rows.append({
            "nodeId": node_id,
            "title": node.title or "Assistant reply",
            "provider": provider,
            "model": model,
            "estimatedInputTokens": max(0, int(estimate)),
        })
        proof.append((
            node_id, parent_id, node.content, node.state.version_index,
            node.state.reply_history_fingerprint,
            fingerprint_for_reply(document, parent_id, provider, model, bool(node.state.reply_knowledge_enabled)),
        ))
    token = hashlib.sha256(json.dumps(proof, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "previewToken": token,
        "replies": rows,
        "count": len(rows),
        "requiresConfirmation": len(rows) > 10,
    }


def _apply_reply(document, node_id: str, answer: str, provider, model, fingerprint, knowledge_enabled):
    """Only the chat node changes; content children and other kinds survive."""
    node = document.nodes[node_id]
    document.update_chat_node_content(node_id, answer)
    _stamp_reply_provenance(node, provider, model)
    node.state.reply_history_fingerprint = fingerprint
    node.state.reply_knowledge_enabled = knowledge_enabled
    apply_reply_citations(node, answer)
    document.sync_chat_version(node_id)


def _rerun_snapshot(document, node_id: str):
    """One live node's model-visible input and structural ancestry.

    Keep the object identity to reject a delete/recreate with the same ID.
    Historical, unselected alternatives never enter this guard or the reply
    fingerprint: only the current projected message belongs to the context.
    """
    node = document.nodes.get(node_id)
    if node is None:
        return None
    edge = document._branch_parent_edge(node_id)
    parent_id = edge.source if edge is not None else None
    if parent_id is not None and parent_id not in document.nodes:
        return None
    chat = isinstance(node.state, ChatState)
    return (
        node, parent_id, _message_input(node),
        node.state.version_index if chat else None,
        len(node.state.versions) if chat else None,
        node.state.provider if chat else None,
        node.state.model if chat else None,
        node.state.reply_history_fingerprint if chat else None,
        node.state.reply_knowledge_enabled if chat else None,
    )


def _capture_rerun_inputs(document, reply_ids: list[str]) -> dict[str, tuple]:
    """Snapshot every planned reply AND its ancestors before the first await."""
    snapshots: dict[str, tuple] = {}
    for reply_id in reply_ids:
        seen: set[str] = set()
        cursor: str | None = reply_id
        while cursor is not None:
            if cursor in seen:
                raise SceneError("The re-run ancestry contains a cycle.")
            seen.add(cursor)
            if cursor not in snapshots:
                current = _rerun_snapshot(document, cursor)
                if current is None:
                    raise SceneError("The re-run path changed. Preview it again.")
                snapshots[cursor] = current
            cursor = snapshots[cursor][1]
    return snapshots


def _rerun_path_unchanged(document, reply_id: str, snapshots: dict[str, tuple]) -> bool:
    """Re-check the actual path: edits, deletes, reparenting and switches abort."""
    cursor: str | None = reply_id
    seen: set[str] = set()
    while cursor is not None and cursor not in seen:
        old = snapshots.get(cursor)
        current = _rerun_snapshot(document, cursor)
        if old is None or current is None or current[0] is not old[0] or current[1:] != old[1:]:
            return False
        seen.add(cursor)
        cursor = current[1]
    return cursor is None


def register_chat_rerun_intents(
    bus, document, notifications, agent_dispatcher, composer_document,
    token_counter, publish_scene, publish_token_counter,
) -> None:
    """A consented, single-undo batch; no undo stack gap while awaiting a reply."""
    from backend.canvas import _history_image_tokens, _history_token_text

    jobs: dict[str, dict] = {}
    active: set[str] = set()
    # Server-issued, single-use preview grants. A deterministic plan digest
    # alone is forgeable and does not establish that a user saw a preview.
    previews: dict[str, tuple[str, str, str, float]] = {}

    async def preview_chat_rerun(node_id, scope="this_path"):
        plan = _preview_plan(document, agent_dispatcher, bus, node_id, scope)
        token = uuid.uuid4().hex
        if len(previews) >= 128:
            previews.pop(next(iter(previews)))
        previews[token] = (node_id, scope, plan["previewToken"], time.monotonic() + 600)
        return {**plan, "previewToken": token}

    async def get_chat_rerun_status(job_id):
        if not isinstance(job_id, str) or job_id not in jobs:
            raise SceneError("Unknown chat re-run job.")
        job = jobs[job_id]
        return {
            key: job[key] for key in (
                "jobId", "status", "completed", "total", "currentNodeId", "error", "cancelRequested",
            )
        }

    async def cancel_chat_rerun(job_id):
        job = jobs.get(job_id) if isinstance(job_id, str) else None
        if job is None or job["status"] != "running":
            return {"ok": False}
        job["cancelRequested"] = True
        if job["requestId"]:
            agent_dispatcher.cancel(job["requestId"])
        return {"ok": True}

    async def start_chat_rerun(node_id, scope, preview_token, confirm_large=False):
        if active:
            raise SceneError("A chat re-run is already in progress.")
        grant = previews.get(preview_token) if isinstance(preview_token, str) else None
        if grant is None or grant[0:2] != (node_id, scope) or time.monotonic() > grant[3]:
            raise SceneError("Preview the replies before confirming a re-run.")
        plan = _preview_plan(document, agent_dispatcher, bus, node_id, scope)
        if plan["previewToken"] != grant[2]:
            raise SceneError("The chat changed since the preview. Preview it again.")
        if not plan["count"]:
            raise SceneError("There are no ordinary chat replies to re-run.")
        if plan["requiresConfirmation"] and confirm_large is not True:
            raise SceneError("Confirm explicitly before re-running more than 10 replies.")
        if any(document.nodes[row["nodeId"]].pending_request_id for row in plan["replies"]):
            raise SceneError("A selected reply is already generating.")
        if getattr(agent_dispatcher, "_runs", None) is not None and agent_dispatcher._runs.is_busy("chat"):
            raise SceneError("Another chat reply is already generating.")

        reply_ids = [row["nodeId"] for row in plan["replies"]]
        snapshots = _capture_rerun_inputs(document, reply_ids)
        # Do not consume consent on failed admission. Once accepted, a retry
        # needs a newly reviewed plan (including the >10-reply acknowledgement).
        previews.pop(preview_token, None)
        job_id = uuid.uuid4().hex
        job = {
            "jobId": job_id, "status": "running", "completed": 0,
            "total": plan["count"], "currentNodeId": None, "error": "",
            "cancelRequested": False, "requestId": None,
        }
        if len(jobs) >= 128:
            finished = next((key for key, value in jobs.items() if value["status"] != "running"), None)
            if finished is not None:
                jobs.pop(finished)
        jobs[job_id] = job
        active.add(job_id)
        # The first completed reply installs one command. Every subsequent
        # reply REPLACES that top entry with a larger merged command. No gap
        # exists where another intent can undo an unrecorded mutation.
        batch = {
            "command": None,
            "priorHead": document.command_log[-1] if document.command_log else None,
            "priorLength": len(document.command_log),
        }

        def stack_unchanged():
            if batch["command"] is not None:
                # A user command that was added and then undone is still a
                # conflicting edit. Never merge a new reply while its redo
                # entry could later replay over the batch.
                return (
                    bool(document.command_log)
                    and document.command_log[-1] is batch["command"]
                    and not document.redo_stack
                )
            return (
                len(document.command_log) == batch["priorLength"]
                and (document.command_log[-1] if document.command_log else None) is batch["priorHead"]
            )

        async def _execute():
            try:
                for row in plan["replies"]:
                    if job["cancelRequested"]:
                        break
                    node_id_to_run = row["nodeId"]
                    node = document.nodes.get(node_id_to_run)
                    if (
                        node is None or not isinstance(node.state, ChatState)
                        or node.state.is_user or node.state.is_branch_synthesis
                        or not stack_unchanged()
                        or not _rerun_path_unchanged(document, node_id_to_run, snapshots)
                    ):
                        raise SceneError("The re-run path or undo history changed. Preview it again.")
                    edge = document._branch_parent_edge(node_id_to_run)
                    if edge is None:
                        raise SceneError("A selected reply no longer has a parent.")
                    parent_id = edge.source
                    job["currentNodeId"] = node_id_to_run
                    knowledge = await _use_knowledge_for_node(document, parent_id)
                    if job["cancelRequested"]:
                        break
                    if not stack_unchanged() or not _rerun_path_unchanged(document, node_id_to_run, snapshots):
                        raise SceneError("The chat changed during re-run. Completed replies were kept.")
                    history = document.chat_branch_history(parent_id)
                    provider, model = _snapshot_active_provider_model(
                        agent_dispatcher, document, parent_id, bus,
                    )
                    provider = provider or node.state.provider or ""
                    model = model or node.state.model or ""
                    if (provider, model) != (row["provider"], row["model"]):
                        raise SceneError("The reply model changed since the preview. Preview it again.")
                    fingerprint = fingerprint_for_reply(document, parent_id, provider, model, knowledge)
                    history = await _history_with_chat_knowledge(
                        history, _latest_user_query(history), document.current_workspace_id,
                        enabled=knowledge,
                        settings_manager=getattr(agent_dispatcher, "_settings_manager", None),
                    )
                    if job["cancelRequested"]:
                        break
                    if not stack_unchanged() or not _rerun_path_unchanged(document, node_id_to_run, snapshots):
                        raise SceneError("The chat changed during re-run. Completed replies were kept.")
                    token_counter.set_context_text(
                        _history_token_text(history), image_tokens=_history_image_tokens(history),
                    )
                    token_counter.reset_real_usage()
                    await publish_token_counter()
                    if job["cancelRequested"]:
                        break
                    if not stack_unchanged() or not _rerun_path_unchanged(document, node_id_to_run, snapshots):
                        raise SceneError("The chat changed during re-run. Completed replies were kept.")

                    stopped = asyncio.Event()
                    succeeded = {"value": False}
                    committed = {"version": None, "index": None}

                    async def on_reply(answer):
                        if job["cancelRequested"] or not answer or not str(answer).strip():
                            return
                        if not stack_unchanged() or not _rerun_path_unchanged(document, node_id_to_run, snapshots):
                            job["error"] = "The chat changed during re-run; the reply was not overwritten."
                            return
                        token_counter.set_output_text(answer)
                        _, command = document.record_command(
                            "rerunFromHere", "agent",
                            lambda: _apply_reply(
                                document, node_id_to_run, answer, provider, model, fingerprint, knowledge,
                            ),
                            node_ids=[node_id_to_run], push=False,
                        )
                        if command.is_noop:
                            return
                        if batch["command"] is None:
                            document._push_command(command)
                            batch["command"] = command
                        else:
                            merged = _merge_commands("rerunFromHere", "agent", [batch["command"], command])
                            if merged is None or merged.is_noop:
                                raise RuntimeError("The chat re-run command could not be merged.")
                            document.command_log[-1] = merged
                            batch["command"] = merged
                        snapshots[node_id_to_run] = _rerun_snapshot(document, node_id_to_run)
                        committed["version"] = node.state.versions[node.state.version_index]
                        committed["index"] = node.state.version_index
                        succeeded["value"] = True

                    def committed_node_id():
                        live = document.nodes.get(node_id_to_run)
                        version = committed["version"]
                        if (
                            not succeeded["value"] or live is not node or version is None
                            or not stack_unchanged()
                            or live.state.version_index != committed["index"]
                            or not 0 <= live.state.version_index < len(live.state.versions)
                            or live.state.versions[live.state.version_index] is not version
                        ):
                            return None
                        return node_id_to_run

                    def on_begin(request_id):
                        node.pending_request_id = request_id
                        job["requestId"] = request_id
                        if job["cancelRequested"]:
                            agent_dispatcher.cancel(request_id)

                    def on_end():
                        node.pending_request_id = None
                        job["requestId"] = None
                        stopped.set()

                    await agent_dispatcher.start_chat_reply(
                        bus=bus, notifications_state=notifications,
                        composer_document=composer_document,
                        conversation_history=history,
                        on_reply=on_reply,
                        on_usage=_make_on_usage(
                            document, token_counter, publish_token_counter, publish_scene,
                            committed_node_id, provider, model,
                            command_ref=lambda: batch["command"],
                        ),
                        # A cancelled/failed stream must keep the last full answer.
                        on_partial=lambda _text: None,
                        on_begin=on_begin, on_end=on_end,
                        state_topic="scene", stream=True,
                        canvas_document=document, node_id=parent_id,
                    )
                    await asyncio.sleep(0)
                    if not stopped.is_set() and job["requestId"] is None:
                        raise SceneError("The model did not start this reply.")
                    await stopped.wait()
                    if succeeded["value"]:
                        job["completed"] += 1
                        await publish_scene()
                    if job["error"]:
                        raise SceneError(job["error"])
                    if job["cancelRequested"]:
                        break
                    if not succeeded["value"]:
                        raise SceneError("The reply was interrupted; its previous answer was kept.")
                job["status"] = "cancelled" if job["cancelRequested"] else "completed"
            except asyncio.CancelledError:
                # Session teardown may cancel the task rather than invoke the
                # normal cooperative intent. Keep already-committed work
                # undoable and do not leave a permanently 'running' job.
                job["cancelRequested"] = True
                job["status"] = "cancelled"
                if job["requestId"]:
                    agent_dispatcher.cancel(job["requestId"])
                raise
            except Exception as exc:
                _logger.exception("Chat re-run failed")
                job["status"] = "failed"
                job["error"] = str(exc)
            finally:
                job["currentNodeId"] = None
                job["requestId"] = None
                active.discard(job_id)
                await publish_scene()

        asyncio.create_task(_execute())
        return {"jobId": job_id}

    bus.register_intent("scene", "previewChatRerun", preview_chat_rerun)
    bus.register_intent("scene", "startChatRerun", start_chat_rerun)
    bus.register_intent("scene", "getChatRerunStatus", get_chat_rerun_status)
    bus.register_intent("scene", "cancelChatRerun", cancel_chat_rerun)
