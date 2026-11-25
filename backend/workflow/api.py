"""Authenticated detached workflow host routes.

The app's existing /api capability, origin and host checks cover every route.
No active SessionBus or SceneDocument is resolved or mutated.
"""
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response

from backend.asset_store import ALLOWED_IMAGE_MIME_TYPES
from backend.domain.model import SceneError
from backend.workspace_archive import _collect_asset_refs
from .engine import RunLimits, WorkflowEngine
from .automation import WorkflowAutomation
from .planning import PolicyResolver
from .registry import StepAdapterRegistry
from .wire import decode_request

_MAX_FILE_BYTES = 64 * 1024 * 1024


class WorkflowService:
    """Lazy so unrelated app instances do not open or migrate a chat database."""

    def __init__(self, db_path: Path, *, limits: RunLimits | None = None,
                 registry: StepAdapterRegistry | None = None, policy_for: PolicyResolver | None = None):
        self.db_path = db_path
        self.limits = limits
        self.registry = registry
        self.policy_for = policy_for
        self._engine: WorkflowEngine | None = None
        self.on_pause = None
        self.automation = WorkflowAutomation(self)

    def engine(self) -> WorkflowEngine:
        if self._engine is None:
            self._engine = WorkflowEngine(self.db_path, limits=self.limits, registry=self.registry,
                                          policy_for=self.policy_for, on_pause=self.on_pause)
        return self._engine

    def pending_approvals(self):
        return self._engine.pending_approvals() if self._engine is not None else []

    async def shutdown(self) -> None:
        await self.automation.stop()
        if self._engine is not None:
            await self._engine.shutdown()


def _checked(operation: Callable):
    try:
        return operation()
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except (ValueError, KeyError, TypeError, SceneError) as exc:
        raise HTTPException(400, str(exc)) from None
    except sqlite3.IntegrityError:
        raise HTTPException(400, "The workflow workspace does not exist.") from None


def _require_run(engine: WorkflowEngine, workspace_id: int, run_id: str):
    record = engine.store.read(workspace_id, run_id)
    if record is None:
        raise HTTPException(404, "Workflow run not found.")
    return record


def _run_asset_refs(record: dict[str, Any]) -> set[str]:
    values = [*record["inputs"].values(), *record["outputs"].values()]
    for step in record["steps"].values():
        values.extend(step["outputs"].values())
        for item in step.get("map_items", {}).values():
            values.extend(item["outputs"].values())
            for child in item["steps"].values():
                values.extend(child["outputs"].values())
    return {value["asset_ref"] for value in values if "asset_ref" in value}


def _download(engine: WorkflowEngine, ref: str, refs: set[str]) -> Response:
    if ref not in refs:
        raise HTTPException(404, "Workflow asset not found.")
    data = engine.artifacts.store.get(ref)
    if data is None:
        raise HTTPException(404, "Workflow asset not found.")
    return Response(data, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{ref}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    })


def register_workflow_api(app: FastAPI, service: WorkflowService) -> None:
    prefix = "/api/workflows/{workspace_id}"

    @app.get("/api/workflows/active-approvals")
    async def active_approvals():
        return service.pending_approvals()

    @app.get("/api/workflows/workspaces")
    async def workspaces():
        from backend.chat_library import get_all_workspaces
        return [{"id": row["id"], "name": row["name"]} for row in get_all_workspaces(service.db_path)]

    @app.post(prefix + "/preview")
    async def preview(workspace_id: int, body: dict[str, Any]):
        return _checked(lambda: asdict(service.engine().preview(*decode_request(body), workspace_id=workspace_id)))

    @app.post(prefix + "/approve")
    async def approve(workspace_id: int, body: dict[str, Any]):
        if body.get("approved") is not True or not isinstance(body.get("fingerprint"), str):
            raise HTTPException(400, "Explicit approval of a workflow preview is required.")
        return _checked(lambda: {"approval_token": service.engine().approve_fingerprint(
            workspace_id, body["fingerprint"],
        )})

    @app.post(prefix + "/runs", status_code=202)
    async def start(workspace_id: int, body: dict[str, Any]):
        return _checked(lambda: {"run_id": service.engine().start(
            workspace_id, *decode_request(body), approval_token=body.get("approval_token"),
        )})

    @app.get(prefix + "/runs")
    async def list_runs(workspace_id: int, summary: bool = False):
        # Resolve the single engine on its owning event loop, then move the
        # synchronous SQLite read and JSON decoding onto a worker.
        store = service.engine().store
        return await asyncio.to_thread(store.list_summaries if summary else store.list, workspace_id)

    @app.get(prefix + "/runs/{run_id}")
    async def get_run(workspace_id: int, run_id: str):
        record = await asyncio.to_thread(service.engine().store.read_visible, workspace_id, run_id)
        if record is None:
            raise HTTPException(404, "Workflow run not found.")
        return record

    @app.post(prefix + "/runs/{run_id}/cancel")
    async def cancel(workspace_id: int, run_id: str):
        engine = service.engine()
        _require_run(engine, workspace_id, run_id)
        if not engine.cancel(workspace_id, run_id):
            raise HTTPException(409, "This workflow is not running.")
        return {"cancel_requested": True}

    @app.post(prefix + "/runs/{run_id}/step-approval")
    async def step_approval(workspace_id: int, run_id: str, body: dict[str, Any]):
        if type(body.get("approved")) is not bool or not isinstance(body.get("step_id"), str):
            raise HTTPException(400, "Explicit approval or rejection of a pending step is required.")
        engine = service.engine()
        _require_run(engine, workspace_id, run_id)
        if not engine.decide_step(workspace_id, run_id, body["step_id"], body["approved"]):
            raise HTTPException(409, "This step is not awaiting approval.")
        return {"decision_recorded": True}

    @app.post(prefix + "/runs/{run_id}/save-as-graph", status_code=201)
    async def save_as_graph(workspace_id: int, run_id: str, body: dict[str, Any] | None = None):
        engine = service.engine()
        _require_run(engine, workspace_id, run_id)
        title = (body or {}).get("title")
        if title is not None and not isinstance(title, str):
            raise HTTPException(400, "Graph title must be text.")
        return _checked(lambda: {"graph_id": engine.save_result_as_graph(workspace_id, run_id, title=title)})

    @app.post(prefix + "/presets/preview")
    async def preview_preset(workspace_id: int, body: dict[str, Any]):
        return _checked(lambda: service.automation.preview(body))

    @app.post(prefix + "/presets", status_code=201)
    async def save_preset(workspace_id: int, body: dict[str, Any]):
        return _checked(lambda: service.automation.save(workspace_id, body))

    @app.get(prefix + "/presets")
    async def list_presets(workspace_id: int, summary: bool = False):
        automation = service.automation
        return await asyncio.to_thread(automation.list_summaries if summary else automation.list, workspace_id)

    @app.delete(prefix + "/presets/{preset_id}")
    async def delete_preset(workspace_id: int, preset_id: str):
        if not service.automation.delete(workspace_id, preset_id):
            raise HTTPException(404, "Workflow preset not found.")
        return {"deleted": True}

    @app.post(prefix + "/presets/{preset_id}/run", status_code=202)
    async def run_preset(workspace_id: int, preset_id: str):
        return _checked(lambda: {"run_id": service.automation.run(workspace_id, preset_id)})

    @app.post(prefix + "/runs/{run_id}/step-decision")
    async def step_decision(workspace_id: int, run_id: str, body: dict[str, Any]):
        if (set(body) != {"step_id", "nonce", "approved"} or type(body["approved"]) is not bool
                or not isinstance(body["step_id"], str) or not isinstance(body["nonce"], str)):
            raise HTTPException(400, "Review the pending step and supply an explicit decision.")
        _checked(lambda: service.engine().decide_step(
            workspace_id, run_id, body["step_id"], body["nonce"], approved=body["approved"],
        ))
        return {"accepted": True}

    @app.post(prefix + "/files", status_code=201)
    async def file_parameter(workspace_id: int, request: Request, filename: str,
                             media_type: str = "application/octet-stream", image: bool = False):
        if not filename or len(filename) > 255 or len(media_type) > 255:
            raise HTTPException(400, "Invalid workflow file metadata.")
        if image and media_type not in ALLOWED_IMAGE_MIME_TYPES:
            raise HTTPException(400, "Choose a supported image media type.")
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > _MAX_FILE_BYTES:
                raise HTTPException(413, "Workflow file exceeds the 64 MiB import limit.")
            data.extend(chunk)
        value = service.engine().artifacts.file(bytes(data), filename, media_type, image=image)
        return asdict(value)

    @app.get(prefix + "/runs/{run_id}/assets/{ref}")
    async def run_asset(workspace_id: int, run_id: str, ref: str):
        engine = service.engine()
        return _download(engine, ref, _run_asset_refs(_require_run(engine, workspace_id, run_id)))

    @app.get(prefix + "/graphs/{graph_id}/assets/{ref}")
    async def graph_asset(workspace_id: int, graph_id: int, ref: str):
        from backend.chat_library import load_chat_row
        engine = service.engine()
        graph = load_chat_row(service.db_path, graph_id)
        if graph is None or graph.get("workspace_id") != workspace_id:
            raise HTTPException(404, "Graph not found.")
        refs: set[str] = set()
        _collect_asset_refs(graph["data"], refs)
        return _download(engine, ref, refs)
