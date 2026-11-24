"""Operator-owned presets and in-process, non-replaying workflow triggers."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend import workflow_library_store as library
from .artifacts import canonical, digest
from .detached_store import utc_now
from .engine import UnattendedPolicy
from .library import configured_spec, typed_inputs
from .wire import decode_request

log = logging.getLogger(__name__)
POLL_SECONDS = 2
MAX_PRESETS = 100
MAX_WATCH_ENTRIES = 10_000


def validate_trigger(trigger, db_path: Path):
    if trigger is None:
        return None
    if not isinstance(trigger, dict):
        raise ValueError("Choose a valid trigger.")
    kind = trigger.get("kind")
    if kind == "interval" and set(trigger) == {"kind", "seconds"}:
        seconds = trigger["seconds"]
        if type(seconds) is int and 10 <= seconds <= 31 * 86400:
            return dict(trigger)
    elif kind == "daily" and set(trigger) == {"kind", "time"}:
        value = trigger["time"]
        if isinstance(value, str) and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
            return dict(trigger)
    elif kind == "folder" and set(trigger) == {"kind", "path"}:
        if not isinstance(trigger["path"], str) or not trigger["path"]:
            raise ValueError("Choose an existing watched folder.")
        folder = Path(trigger["path"]).expanduser().resolve()
        if not folder.is_dir() or folder == db_path.resolve() or folder in db_path.resolve().parents:
            raise ValueError("Choose an existing folder outside the Graphlink database directory.")
        return {"kind": kind, "path": str(folder)}
    raise ValueError("Invalid trigger: use interval (10 seconds or more), daily HH:MM, or a folder.")


def folder_snapshot(folder: Path):
    """Metadata-only recursive snapshot. Never read contents or follow symlinks."""
    if not folder.is_dir() or folder.is_symlink() or folder.resolve() != folder:
        return None
    rows = []

    def fail(error):
        raise error

    try:
        for root, dirs, files in os.walk(folder, followlinks=False, onerror=fail):
            dirs[:] = sorted(d for d in dirs if not (Path(root) / d).is_symlink())
            for name in sorted(dirs + files):
                target = Path(root) / name
                if target.is_symlink():
                    continue
                info = target.stat(follow_symlinks=False)
                rows.append((str(target.relative_to(folder)), info.st_mtime_ns, info.st_size))
                if len(rows) > MAX_WATCH_ENTRIES:
                    return None
    except OSError:
        return None
    return digest(sorted(rows))


def next_daily(value: str, now: datetime) -> datetime:
    hour, minute = map(int, value.split(":"))
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return due if due > now else due + timedelta(days=1)


class WorkflowAutomation:
    def __init__(self, service):
        self.service = service
        self.db_path = Path(service.db_path)
        self.task: asyncio.Task | None = None
        self._baselines: dict[str, Any] = {}

    @contextmanager
    def connection(self):
        from backend.chat_library import _connect
        with closing(_connect(self.db_path)) as conn, conn:
            yield conn

    def library_entries(self):
        with self.connection() as conn:
            return library.list_library(conn)

    def resolve(self, request: dict):
        # Local detached specs are accepted only by the operator preset API.
        # The portable library continues to reject effectful imports.
        if "spec" in request:
            if set(request) != {"spec", "parameters"}:
                raise ValueError("Invalid detached workflow request.")
            spec, params = decode_request(request)
            if any(value.kind in ("file", "image") for value in params.values()):
                raise ValueError("Presets currently support text, JSON and table inputs.")
            return spec, params
        if set(request) != {"workflow_id", "revision", "inputs", "parameters"}:
            raise ValueError("Choose an exact library revision, inputs and parameters.")
        with self.connection() as conn:
            if not any(row["workflow_id"] == request["workflow_id"] for row in library.list_library(conn)):
                raise ValueError("Workflow is no longer in the library.")
            document = library.get(conn, request["workflow_id"], request["revision"])
        spec = configured_spec(document, request["parameters"])
        return spec, typed_inputs(spec, request["inputs"])

    def preview(self, request: dict):
        return asdict(self.service.engine().preview(*self.resolve(request)))

    def save(self, workspace_id: int, body: dict):
        if set(body) != {"request", "name", "allowed_effects", "cost_usd", "trigger", "fingerprint", "approved"}:
            raise ValueError("Invalid preset fields.")
        if body["approved"] is not True:
            raise PermissionError("Review and approve the preset in the operator UI.")
        name = body["name"]
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("Choose a preset name (maximum 200 characters).")
        effects = body["allowed_effects"]
        if not isinstance(effects, list) or not all(isinstance(e, str) for e in effects):
            raise ValueError("Choose allowed side-effect classes.")
        policy = UnattendedPolicy(body["fingerprint"], frozenset(effects), body["cost_usd"])
        request = body["request"]
        if not isinstance(request, dict) or len(canonical(request).encode()) > 1_000_000:
            raise ValueError("Preset request must be a JSON object under 1 MB.")
        preview = self.preview(request)
        if preview["fingerprint"] != policy.fingerprint:
            raise PermissionError("The preview changed; review it again.")
        if preview["estimated_cost_usd"] > min(policy.cost_usd, self.service.engine().limits.cost_usd):
            raise ValueError("The preview exceeds the preset or backend cost cap.")
        trigger = validate_trigger(body["trigger"], self.db_path)
        if trigger and trigger["kind"] == "folder" and folder_snapshot(Path(trigger["path"])) is None:
            raise ValueError("The watched folder is unreadable or exceeds the entry limit.")
        preset = {"preset_id": uuid.uuid4().hex, "workspace_id": workspace_id,
                  "name": name.strip(), "request": request, "allowed_effects": sorted(policy.allowed_effects),
                  "cost_usd": policy.cost_usd, "trigger": trigger, "fingerprint": policy.fingerprint,
                  "workflow_id": request.get("workflow_id", request.get("spec", {}).get("workflow_id")),
                  "revision": request.get("revision", request.get("spec", {}).get("revision")),
                  "created_at": utc_now()}
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT count(*) FROM workflow_presets").fetchone()[0] >= MAX_PRESETS:
                raise ValueError("Delete an unused preset before creating another.")
            conn.execute("INSERT INTO workflow_presets VALUES (?, ?, ?)",
                         (preset["preset_id"], workspace_id, canonical(preset)))
        return preset

    def list(self, workspace_id: int | None = None):
        with self.connection() as conn:
            if workspace_id is None:
                rows = conn.execute("SELECT payload FROM workflow_presets ORDER BY preset_id")
            else:
                rows = conn.execute("SELECT payload FROM workflow_presets WHERE workspace_id=? ORDER BY preset_id",
                                    (workspace_id,))
            return [json.loads(row[0]) for row in rows]

    def delete(self, workspace_id: int, preset_id: str):
        with self.connection() as conn:
            deleted = conn.execute("DELETE FROM workflow_presets WHERE workspace_id=? AND preset_id=?",
                                   (workspace_id, preset_id)).rowcount
        self._baselines.pop(preset_id, None)
        return bool(deleted)

    def run(self, workspace_id: int, preset_id: str, *, operation_id: str | None = None,
            request: dict | None = None):
        preset = next((p for p in self.list(workspace_id) if p["preset_id"] == preset_id), None)
        if preset is None:
            raise ValueError("Workflow preset not found.")
        if request is not None and canonical(request) != canonical(preset["request"]):
            raise PermissionError("Parameters differ from the approved preset; review a new preset in the app.")
        engine = self.service.engine()
        if operation_id is not None:
            if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", operation_id):
                raise ValueError("Use an operation ID of 8–100 letters, digits, underscores or hyphens.")
            for run in engine.store.list(workspace_id):
                if run.get("operation_id") == operation_id:
                    if run.get("preset_id") != preset_id:
                        raise ValueError("Operation ID already belongs to another preset.")
                    return run["run_id"]
        policy = UnattendedPolicy(preset["fingerprint"], frozenset(preset["allowed_effects"]), preset["cost_usd"])
        return engine.start(workspace_id, *self.resolve(preset["request"]), unattended=policy,
                            preset_id=preset_id, operation_id=operation_id)

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._loop())

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        self._baselines.clear()

    async def tick(self, *, monotonic: float | None = None, now: datetime | None = None):
        # Do not create a database just to discover that no presets exist.
        if not self.db_path.is_file():
            return
        presets = await asyncio.to_thread(self.list)
        live = {p["preset_id"] for p in presets}
        self._baselines = {key: value for key, value in self._baselines.items() if key in live}
        monotonic = time.monotonic() if monotonic is None else monotonic
        now = datetime.now() if now is None else now
        for preset in presets:
            trigger = preset["trigger"]
            if trigger is None:
                continue
            key = preset["preset_id"]
            old = self._baselines.get(key)
            kind = trigger["kind"]
            fire = False
            if kind == "interval":
                if old is None or monotonic >= old:
                    fire = old is not None and monotonic - old <= POLL_SECONDS * 2
                    self._baselines[key] = monotonic + trigger["seconds"]
            elif kind == "daily":
                if old is None or now >= old:
                    fire = old is not None and (now - old).total_seconds() <= POLL_SECONDS * 2
                    self._baselines[key] = next_daily(trigger["time"], now)
            elif kind == "folder":
                current = await asyncio.to_thread(folder_snapshot, Path(trigger["path"]))
                fire = old is not None and current is not None and old != current
                self._baselines[key] = current
            if fire:
                try:
                    # Advance the baseline before admission. A busy/deleted
                    # workflow is skipped, not enqueued or caught up later.
                    self.run(preset["workspace_id"], key)
                except (ValueError, PermissionError, KeyError):
                    log.info("Skipped workflow preset trigger (busy, changed or invalid preset).")

    async def _loop(self):
        while True:
            try:
                await self.tick()
            except Exception:
                log.warning("Workflow trigger scan failed; no runs replayed.")
                self._baselines.clear()
            await asyncio.sleep(POLL_SECONDS)
