"""Preview and cooperatively execute ordinary chat-reply reruns.

Batch progress lives in this session's intent closure, not in the saved scene
wire. The already-versioned chat state is the only persistent mutation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
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
from backend.context_engine.citations import CITATION_RECORD_NAME, apply_reply_citations
from backend.domain.commands import _merge_commands
from backend.domain.graph_runner import bound_dependents, is_bound_node
from backend.domain.model import SceneError
from backend.domain.node_states import ChatState
from backend.domain.staleness import (
    fingerprint_for_reply, fingerprint_from_history_hashes, history_hashes, scene_staleness,
)
from backend.domain.structure import primary_parent_edges

_logger = logging.getLogger(__name__)
_SCOPES = ("this_path", "all_branches_below")


def _has_executed_tools(node) -> bool:
    """Citation provenance is metadata, but any other tool call is unsafe to replay."""
    return isinstance(node.state, ChatState) and any(
        not isinstance(call, dict) or call.get("name") != CITATION_RECORD_NAME
        for call in node.state.tool_invocations
    )


def _rerun_selection(document, root_id: str, scope: str) -> tuple[list[str], list[dict]]:
    """One canonical traversal for BOTH the executable batch and skipped preview."""
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
    if _has_executed_tools(root):
        raise SceneError("Tool-executing replies must be re-run manually.")

    chosen = primary_parent_edges(document.nodes, document.edges, set(document.nodes))
    children: dict[str, list[str]] = {}
    for child_id, edge in chosen.items():
        children.setdefault(edge.source, []).append(child_id)

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

    replies: list[str] = []
    skipped: list[dict] = []
    stack = [(root_id, False)]
    visited: set[str] = set()
    while stack:
        item, blocked_upstream = stack.pop()
        if item in visited:
            continue
        visited.add(item)
        node = document.nodes.get(item)
        if node is None:
            continue
        # A skipped executable or bound node is a dependency boundary.
        # Never generate a downstream reply using stale tool/runner output.
        blocked = blocked_upstream
        if node.kind == "chat" and isinstance(node.state, ChatState):
            if not node.state.is_user:
                if _has_executed_tools(node):
                    reason = "This reply executed tools; re-run it manually."
                elif node.state.is_branch_synthesis:
                    reason = "Branch synthesis requires its separate manual action."
                elif blocked:
                    reason = "An upstream node was skipped; re-run this reply manually."
                else:
                    reason = ""
                if reason:
                    skipped.append({
                        "nodeId": item, "title": node.title or "Assistant reply",
                        "kind": node.kind, "category": "manual", "reason": reason,
                    })
                    blocked = True
                else:
                    replies.append(item)
        elif item != root_id:
            bound = is_bound_node(node)
            if bound:
                reason = "Data-bound output requires Graph Runner preview and approval."
            else:
                names = {
                    "harness": "Workspace agent", "plan": "Builder",
                    "code_sandbox": "Code sandbox",
                }
                name = names.get(node.kind, "Plugin action" if "." in node.kind else "Tool or other")
                reason = f"{name} nodes may have side effects; never re-run automatically. Re-run manually."
            skipped.append({
                "nodeId": item, "title": node.title or node.kind,
                "kind": node.kind, "category": "bound" if bound else "manual",
                "reason": reason,
            })
            blocked = True
        descendants = children.get(item, ())
        if scope == "this_path":
            next_id = next((child for child in descendants if child in active_path), None)
            if next_id is None:
                next_id = next(
                    (child for child in descendants if document.nodes[child].kind == "chat"),
                    next(iter(descendants), None),
                )
            if next_id is not None:
                stack.append((next_id, blocked))
        else:
            stack.extend((child, blocked) for child in reversed(descendants))

    # Binding edges are not necessarily structural canvas edges. Include
    # downstream Model, table and Scientific outputs even when they cannot
    # be discovered by the chat tree. Selection only; no evaluation here.
    already_skipped = {row["nodeId"] for row in skipped}
    for item in bound_dependents(document, visited):
        if item not in already_skipped:
            node = document.nodes[item]
            skipped.append({
                "nodeId": item, "title": node.title or node.kind,
                "kind": node.kind, "category": "bound",
                "reason": "Data-bound output requires Graph Runner preview and approval.",
            })
            already_skipped.add(item)
    return replies, skipped


def _rerun_ids(document, root_id: str, scope: str) -> list[str]:
    """Only ordinary, tool-free replies may enter the actual execution batch."""
    replies, _ = _rerun_selection(document, root_id, scope)
    return replies


def _preview_plan(document, dispatcher, bus, root_id: str, scope: str):
    """The same server-owned selection is recomputed at execution time."""
    from backend.canvas import _history_image_tokens, _history_token_text

    ids, skipped = _rerun_selection(document, root_id, scope)
    rows = []
    root = document.nodes[root_id]
    proof = [root_id, scope, root.content, root.state.version_index, len(root.state.versions)]
    estimator = TokenEstimator()
    # The preview token must check every selected parent, but a single
    # 500-node lineage pass suffices for all replies (not N full passes).
    hashes = history_hashes(document)
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
            fingerprint_from_history_hashes(hashes, parent_id, provider, model, bool(node.state.reply_knowledge_enabled)),
        ))
    # Skipped-node classification is part of the approval token: it must not
    # silently change between the read-only preview and the batch start.
    proof.extend((row["nodeId"], row["kind"], row["category"], row["reason"],
                  document.nodes[row["nodeId"]].content) for row in skipped)
    token = hashlib.sha256(json.dumps(proof, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "previewToken": token,
        "replies": rows,
        "skipped": skipped,
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


def register_chat_rerun_intents(
    bus, document, notifications, agent_dispatcher, composer_document,
    token_counter, publish_scene, publish_token_counter,
) -> None:
    """Separate registration so register_chat_intents stays below its ratchet."""
    from backend.canvas import _history_image_tokens, _history_token_text

    jobs: dict[str, dict] = {}
    active: set[str] = set()

    async def preview_chat_rerun(node_id, scope="this_path"):
        # Tokenizing each selected history can exceed a single event-loop tick
        # on a large graph. The result is still validated at start.
        return await asyncio.to_thread(_preview_plan, document, agent_dispatcher, bus, node_id, scope)

    async def get_chat_rerun_status(job_id):
        if job_id not in jobs:
            raise SceneError("Unknown chat re-run job.")
        job = jobs[job_id]
        return {
            key: job[key] for key in (
                "jobId", "status", "completed", "total", "currentNodeId", "error", "cancelRequested",
            )
        }

    async def cancel_chat_rerun(job_id):
        job = jobs.get(job_id)
        if job is None:
            return {"ok": False}
        if job["status"] != "running":
            return {"ok": False}
        job["cancelRequested"] = True
        if job["requestId"]:
            agent_dispatcher.cancel(job["requestId"])
        return {"ok": True}

    async def start_chat_rerun(node_id, scope, preview_token, confirm_large=False):
        if active:
            raise SceneError("A chat re-run is already in progress.")
        plan = await asyncio.to_thread(_preview_plan, document, agent_dispatcher, bus, node_id, scope)
        # A second start may have won while the tokenization was off-thread.
        if active:
            raise SceneError("A chat re-run is already in progress.")
        if not isinstance(preview_token, str) or plan["previewToken"] != preview_token:
            raise SceneError("The chat changed since the preview. Preview it again.")
        if not plan["count"]:
            raise SceneError("There are no ordinary chat replies to re-run.")
        if plan["requiresConfirmation"] and confirm_large is not True:
            raise SceneError("Confirm explicitly before re-running more than 10 replies.")
        if any(document.nodes[row["nodeId"]].pending_request_id for row in plan["replies"]):
            raise SceneError("A selected reply is already generating.")
        if getattr(agent_dispatcher, "_runs", None) is not None and agent_dispatcher._runs.is_busy("chat"):
            raise SceneError("Another chat reply is already generating.")

        job_id = uuid.uuid4().hex
        job = {
            "jobId": job_id, "status": "running", "completed": 0,
            "total": plan["count"], "currentNodeId": None, "error": "",
            "cancelRequested": False, "requestId": None,
        }
        jobs[job_id] = job
        active.add(job_id)

        async def _execute():
            commands = []
            try:
                for row in plan["replies"]:
                    if job["cancelRequested"]:
                        break
                    node_id_to_run = row["nodeId"]
                    node = document.nodes.get(node_id_to_run)
                    if (
                        node is None or node.kind != "chat"
                        or not isinstance(node.state, ChatState)
                        or node.state.is_user or node.state.is_branch_synthesis
                        or _has_executed_tools(node)
                    ):
                        raise SceneError("The re-run path changed. Preview it again.")
                    edge = document._branch_parent_edge(node_id_to_run)
                    if edge is None:
                        raise SceneError("A selected reply no longer has a parent.")
                    parent_id = edge.source
                    job["currentNodeId"] = node_id_to_run
                    knowledge = await _use_knowledge_for_node(document, parent_id)
                    if job["cancelRequested"]:
                        break
                    history = document.chat_branch_history(parent_id)
                    provider, model = _snapshot_active_provider_model(
                        agent_dispatcher, document, parent_id, bus,
                    )
                    fingerprint = fingerprint_for_reply(document, parent_id, provider, model, knowledge)
                    history = await _history_with_chat_knowledge(
                        history, _latest_user_query(history), document.current_workspace_id,
                        enabled=knowledge,
                        settings_manager=getattr(agent_dispatcher, "_settings_manager", None),
                    )
                    if job["cancelRequested"]:
                        break
                    token_counter.set_context_text(
                        _history_token_text(history), image_tokens=_history_image_tokens(history),
                    )
                    token_counter.reset_real_usage()
                    await publish_token_counter()
                    if job["cancelRequested"]:
                        break

                    stopped = asyncio.Event()
                    succeeded = {"value": False}
                    command_ref = {}

                    async def on_reply(answer):
                        if job["cancelRequested"] or not answer or not str(answer).strip():
                            return
                        if node_id_to_run not in document.nodes:
                            return
                        token_counter.set_output_text(answer)
                        _, command = document.record_command(
                            "rerunFromHere", "agent",
                            lambda: _apply_reply(
                                document, node_id_to_run, answer, provider, model, fingerprint, knowledge,
                            ),
                            node_ids=[node_id_to_run],
                        )
                        # Keep the intermediate scene visible, but consolidate
                        # the commands once at the end into exactly one undo.
                        if not command.is_noop:
                            if document.command_log[-1] is not command:
                                raise RuntimeError("Chat re-run undo stack changed during commit.")
                            document.command_log.pop()
                            commands.append(command)
                            command_ref["command"] = command
                        succeeded["value"] = True

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
                            lambda: node_id_to_run if succeeded["value"] else None, provider, model,
                            command_ref=lambda: command_ref.get("command"),
                        ),
                        # A cancelled/failed stream must NOT replace its
                        # original answer with a partial generation.
                        on_partial=lambda _text: None,
                        on_begin=on_begin, on_end=on_end,
                        state_topic="scene", stream=True,
                        canvas_document=document, node_id=parent_id,
                    )
                    await asyncio.sleep(0)
                    if not stopped.is_set() and job["requestId"] is None:
                        raise SceneError("The model did not start this reply.")
                    await stopped.wait()
                    # A reply that landed just before Cancel is still a
                    # completed reply, even though no next reply may start.
                    if succeeded["value"]:
                        job["completed"] += 1
                        await publish_scene()
                    if job["cancelRequested"]:
                        break
                    if not succeeded["value"]:
                        raise SceneError("The reply was interrupted; its previous answer was kept.")
                job["status"] = "cancelled" if job["cancelRequested"] else "completed"
            except Exception as exc:
                _logger.exception("Chat re-run failed")
                job["status"] = "failed"
                job["error"] = str(exc)
            finally:
                # Merge even on cancel/failure: successfully completed earlier
                # answers remain a SINGLE undoable unit, not lost mutations.
                merged = _merge_commands("rerunFromHere", "agent", commands)
                if merged is not None and not merged.is_noop:
                    document._push_command(merged)
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
