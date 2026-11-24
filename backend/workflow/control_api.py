"""Curated, write-token-only workflow reach. No approval or preset-write routes."""
from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException

from backend.secret_scrub import scrub
from .api import WorkflowService, _checked, _require_run


def register_workflow_control_api(app: FastAPI, service: WorkflowService) -> None:
    @app.get("/api/control/workflows")
    async def list_workflows(workspace_id: int):
        return scrub({
            "workflows": service.automation.library_entries(),
            "presets": [
                {k: p[k] for k in ("preset_id", "name", "workflow_id", "revision",
                                    "allowed_effects", "cost_usd")}
                for p in service.automation.list(workspace_id)
            ],
        })

    @app.post("/api/control/workflows/run", status_code=202)
    async def run_workflow(body: dict[str, Any]):
        def run():
            if (not {"workspace_id", "preset_id", "operation_id"} <= body.keys()
                    or body.keys() - {"workspace_id", "preset_id", "operation_id", "request_json"}
                    or type(body["workspace_id"]) is not int or body["workspace_id"] < 1
                    or not isinstance(body["preset_id"], str)
                    or not isinstance(body["operation_id"], str)):
                raise ValueError("Choose a workspace, preset and retry-safe operation ID.")
            request = None
            if "request_json" in body:
                if not isinstance(body["request_json"], str) or len(body["request_json"]) > 200000:
                    raise ValueError("Invalid preset request JSON.")
                request = json.loads(body["request_json"])
                if not isinstance(request, dict):
                    raise ValueError("Expected a preset request object.")
            return {"run_id": service.automation.run(
                body["workspace_id"], body["preset_id"], operation_id=body["operation_id"], request=request,
            )}
        return _checked(run)

    @app.get("/api/control/workflows/run")
    async def read_run(workspace_id: int, run_id: str):
        run = _require_run(service.engine(), workspace_id, run_id)
        if not run.get("preset_id"):
            raise HTTPException(404, "Preset workflow run not found.")
        # Return only the run receipt and result data, never approval nonces,
        # raw configs/inputs, or the operator's complete preset record.
        return scrub({
            **{k: run[k] for k in ("run_id", "status", "created_at", "started_at", "finished_at",
                                   "cost_usd", "error", "outputs", "steps", "preset_id")},
            "workflow_id": run["spec"]["workflow_id"], "revision": run["spec"]["revision"],
            "awaiting_approval": bool(run.get("awaiting_approval")),
        })
