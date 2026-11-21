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

from api_provider import split_reasoning_and_content
from graphlink_token_estimator import TokenEstimator

from backend.api.intents_chat import (
    _history_with_chat_knowledge,
    _latest_user_query,
    _make_on_usage,
    _snapshot_active_provider_model,
    _snapshot_reply_provenance,
    _stamp_reply_provenance,
    _use_knowledge_for_node,
)
from backend.context_engine.citations import CITATION_RECORD_NAME, apply_reply_citations
from backend.domain.commands import _merge_commands
from backend.domain.graph_runner import bound_dependents, is_bound_node
from backend.domain.model import SceneError
from backend.domain.node_states import ChatState
from backend.domain.model import THINKING_TITLE_PREVIEW_LENGTH
from backend.response_parsing import PLACEHOLDER_ASSISTANT_REASONING, PLACEHOLDER_GENERATED_CONTENT
from backend.domain.staleness import (
    _message_input, fingerprint_for_reply, fingerprint_from_history_hashes,
    history_hashes, scene_staleness,
)
from backend.domain.structure import primary_parent_edges
from backend.token_counter import estimate_cost_usd

_logger = logging.getLogger(__name__)
_SCOPES = ("this_path", "all_branches_below")


def _has_executed_tools(node) -> bool:
    """Citation provenance is metadata, but any other tool call is unsafe to replay."""
    return isinstance(node.state, ChatState) and any(
        not isinstance(call, dict) or call.get("name") != CITATION_RECORD_NAME
        for call in node.state.tool_invocations
    )


def _reply_thinking_ids(document, reply_id: str) -> list[str]:
    """Direct Thinking traces are passive content owned by an assistant reply."""
    return [
        edge.target for edge in document.edges.values()
        if edge.source == reply_id and edge.target in document.nodes
        and document.nodes[edge.target].kind == "thinking"
        and document._branch_parent_edge(edge.target) is edge
    ]


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
        elif node.kind == "thinking":
            # A reasoning trace is part of its reply, not a side-effect
            # boundary. Traverse through it if an older graph continues there.
            pass
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
    manager = getattr(dispatcher, "_settings_manager", None)
    overrides = manager.get_pricing_overrides() if manager is not None else None
    # One lineage pass for the whole preview, not one per reply.
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
        estimate = max(0, int(estimator.count_tokens(_history_token_text(history)) + _history_image_tokens(history)))
        input_cost = estimate_cost_usd(provider, model, estimate, 0, overrides=overrides)
        rows.append({
            "nodeId": node_id,
            "title": node.title or "Assistant reply",
            "provider": provider,
            "model": model,
            "estimatedInputTokens": estimate,
            "estimatedInputCostUsd": input_cost,
        })
        proof.append((
            node_id, parent_id, node.content, node.state.version_index,
            node.state.reply_history_fingerprint,
            fingerprint_from_history_hashes(hashes, parent_id, provider, model, bool(node.state.reply_knowledge_enabled)),
            input_cost,  # Invalidate approval if the displayed input price changes.
            tuple((thought_id, document.nodes[thought_id].content)
                  for thought_id in _reply_thinking_ids(document, node_id)),
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


def _apply_reply(document, node_id: str, answer: str, provider, model, fingerprint, knowledge_enabled, reason=None):
    """Version the visible answer and refresh its Thinking trace in one undo step."""
    node = document.nodes[node_id]
    reasoning, visible_answer = split_reasoning_and_content(answer)
    visible_answer = visible_answer.strip() or (
        PLACEHOLDER_ASSISTANT_REASONING if reasoning else PLACEHOLDER_GENERATED_CONTENT
    )
    document.update_chat_node_content(node_id, visible_answer)
    thinking_ids = _reply_thinking_ids(document, node_id)
    if reasoning:
        if thinking_ids:
            thought = document.nodes[thinking_ids[0]]
            thought.content = reasoning
            thought.title = reasoning[:THINKING_TITLE_PREVIEW_LENGTH] or "Thinking"
        else:
            x, y = document.place_child(node_id, "thinking", prefer="left")
            document.add_thinking_node(x, y, reasoning, parent_id=node_id)
    # Older graphs can have several traces, or even a continuation connected
    # through a Thinking card. Clear redundant traces without orphaning their
    # downstream chat messages; ordinary leaf traces can be removed.
    retired = thinking_ids[1:] if reasoning else thinking_ids
    for thought_id in retired:
        has_children = any(
            edge.source == thought_id and document._branch_parent_edge(edge.target) is edge
            for edge in document.edges.values() if edge.target in document.nodes
        )
        if has_children:
            document.nodes[thought_id].content = ""
            document.nodes[thought_id].title = "Thinking"
        else:
            document.remove_nodes([thought_id])
    _stamp_reply_provenance(node, provider, model, reason)
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
    live = document.nodes.get(reply_id)
    if live is None or _has_executed_tools(live):
        return False
    while cursor is not None and cursor not in seen:
        old = snapshots.get(cursor)
        current = _rerun_snapshot(document, cursor)
        if old is None or current is None or current[0] is not old[0] or current[1:] != old[1:]:
            return False
        seen.add(cursor)
        cursor = current[1]
    return cursor is None


def _job_status(job: dict) -> dict:
    return {
        key: job[key] for key in (
            "jobId", "status", "completed", "total", "currentNodeId", "error", "cancelRequested",
        )
    }


def register_chat_rerun_intents(
    bus, document, notifications, agent_dispatcher, composer_document,
    token_counter, publish_scene, publish_token_counter,
) -> None:
    """A consented, single-undo batch; no undo stack gap while awaiting a reply."""
    from backend.canvas import _history_image_tokens, _history_token_text

    jobs: dict[str, dict] = {}
    active: set[str] = set()
    # Single-use random preview grants, not forgeable plan digests.
    previews: dict[str, tuple[str, str, str, float]] = {}

    async def preview_chat_rerun(node_id, scope="this_path"):
        plan = await asyncio.to_thread(_preview_plan, document, agent_dispatcher, bus, node_id, scope)
        token = uuid.uuid4().hex
        if len(previews) >= 128:
            previews.pop(next(iter(previews)))
        previews[token] = (node_id, scope, plan["previewToken"], time.monotonic() + 600)
        return {**plan, "previewToken": token}

    async def get_chat_rerun_status(job_id, after_completed=None):
        if not isinstance(job_id, str) or job_id not in jobs:
            raise SceneError("Unknown chat re-run job.")
        job = jobs[job_id]
        # A fast batch can finish between UI polls. Deliver every committed
        # milestone to cursor-based readers; legacy callers still get live state.
        if type(after_completed) is int and 0 <= after_completed < len(job["milestones"]):
            return dict(job["milestones"][after_completed])
        return _job_status(job)

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
        plan = await asyncio.to_thread(_preview_plan, document, agent_dispatcher, bus, node_id, scope)
        # Recheck admission after awaiting concurrent work.
        if active:
            raise SceneError("A chat re-run is already in progress.")
        if previews.get(preview_token) is not grant or time.monotonic() > grant[3]:
            raise SceneError("Preview the replies before confirming a re-run.")
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
            "cancelRequested": False, "requestId": None, "milestones": [],
        }
        if len(jobs) >= 128:
            finished = next((key for key, value in jobs.items() if value["status"] != "running"), None)
            if finished is not None:
                jobs.pop(finished)
        jobs[job_id] = job
        active.add(job_id)
        # Every completed reply extends the top undo command; never leave a gap.
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
                for row_index, row in enumerate(plan["replies"]):
                    if job["cancelRequested"]:
                        break
                    node_id_to_run = row["nodeId"]
                    node = document.nodes.get(node_id_to_run)
                    if (
                        node is None or node.kind != "chat"
                        or not isinstance(node.state, ChatState)
                        or node.state.is_user or node.state.is_branch_synthesis
                        or _has_executed_tools(node)
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
                    provider, model, reason = _snapshot_reply_provenance(
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
                                document, node_id_to_run, answer, provider, model, fingerprint, knowledge, reason,
                            ),
                            node_ids=[node_id_to_run, *_reply_thinking_ids(document, node_id_to_run)], push=False,
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
                        for thought_id in _reply_thinking_ids(document, node_id_to_run):
                            snapshots[thought_id] = _rerun_snapshot(document, thought_id)
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
                        job["currentNodeId"] = (
                            plan["replies"][row_index + 1]["nodeId"]
                            if row_index + 1 < len(plan["replies"]) else None
                        )
                        job["milestones"].append(_job_status(job))
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
