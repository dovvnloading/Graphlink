"""Session intents for global Workflow Library and undoable canvas placement."""
from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import dataclass

from backend.api._shared import make_publish_scene
from backend.domain.geometry_safety import finite_float
from backend.domain.model import SceneError
from backend.events import IntentValidationError
from backend.secret_scrub import scrub_text
from backend import workflow_library_store as library
from backend.workflow import canvas
from backend.workflow.library import export_document, import_document


@dataclass
class WorkflowArgs:
    action: str
    payload: dict


def register_workflow_library(bus, document, connect, serialize_mutation):
    publish_scene = make_publish_scene(bus)

    async def db(fn):
        def work():
            with closing(connect()) as conn, conn:
                return fn(conn)
        return await asyncio.to_thread(work)

    async def resolve(workflow_id, revision):
        return await db(lambda conn: library.get(conn, workflow_id, revision))

    async def library_action(action, payload):
        try:
            if action == "preview":
                return canvas.selected_workflow(document, payload["ids"])
            if action == "make":
                if payload.get("expected_spec") != canvas.selected_workflow(document, payload["ids"])["spec"]:
                    raise ValueError("The selected graph changed. Preview Make workflow again.")
                frozen = canvas.selected_workflow(document, payload["ids"], payload["name"],
                                                  payload["outputs"], payload.get("parameters"))
                return await db(lambda c: library.save(c, frozen, name=payload["name"],
                    workflow_id=payload.get("workflow_id"), expected_revision=payload.get("expected_revision")))
            if action == "list":
                return await db(lambda c: library.list_library(c, payload.get("query", "")))
            if action == "import":
                frozen = import_document(payload["text"])
                return await db(lambda c: library.save(c, frozen, name=frozen["spec"]["name"]))
            workflow_id = payload["workflow_id"]
            if action == "history":
                return await db(lambda c: library.history(c, workflow_id))
            if action == "rename":
                await db(lambda c: library.rename(c, workflow_id, payload["name"]))
                return True
            if action == "delete":
                await db(lambda c: library.delete(c, workflow_id))
                return True
            revision = payload["revision"]
            if action == "get":
                return await resolve(workflow_id, revision)
            if action == "export":
                return export_document(await resolve(workflow_id, revision))
            if action == "duplicate":
                return await db(lambda c: library.duplicate(c, workflow_id, revision, payload["name"]))
            raise ValueError("Unknown Workflow Library action.")
        except (ValueError, SceneError) as exc:
            raise IntentValidationError([scrub_text(str(exc))]) from exc
        except (KeyError, TypeError, AttributeError) as exc:
            raise IntentValidationError(["Invalid Workflow Library request."]) from exc

    @serialize_mutation
    async def edit_node(action, payload):
        try:
            node_id = payload.get("node_id")
            node = document.nodes.get(node_id)
            if action in {"insert", "upgrade", "unpack"}:
                wid, rev = payload["workflow_id"], payload["revision"]
                frozen = await resolve(wid, rev)
                history = await db(lambda c: library.history(c, wid))
                version = next(row["version"] for row in history if row["revision"] == rev)
            else:
                frozen, version = None, None
            if action == "insert":
                x, y = finite_float(payload["x"], "x"), finite_float(payload["y"], "y")
                width, height = document.kind_fallback_footprint("note")
                x, y = document.find_free_position(x, y, width, height)
                mutate = lambda: canvas.insert(document, frozen, version, x, y)
            elif action == "unpack":
                x, y = finite_float(payload["x"], "x"), finite_float(payload["y"], "y")
                mutate = lambda: canvas.unpack(document, frozen, payload.get("inputs", {}),
                                                payload.get("parameters", {}), x, y, payload.get("sources", {}))
            elif action == "configure":
                if node is None:
                    raise ValueError("Workflow node no longer exists.")
                if canvas.data(node)["document"]["spec"]["revision"] != payload["expected_revision"]:
                    raise ValueError("The pinned version changed. Reopen the node.")
                mutate = lambda: canvas.configure(document, node, payload["inputs"], payload["parameters"], payload.get("sources", {}))
            elif action == "upgrade":
                if node is None:
                    raise ValueError("Workflow node no longer exists.")
                old = canvas.data(node)
                if (old["document"]["spec"]["workflow_id"] != wid or
                        old["document"]["spec"]["revision"] != payload["expected_revision"] or
                        version <= old["version"]):
                    raise ValueError("The pinned version changed. Preview the upgrade again.")

                def mutate():
                    from backend.domain.node_states import PluginStateSnapshot
                    node.state = PluginStateSnapshot.from_data(canvas.new_state(frozen, version))
                    node.title = frozen["spec"]["name"]
            else:
                raise ValueError("Unknown Workflow node action.")
            if node_id is not None and document.nodes.get(node_id) is not node:
                raise ValueError("Workflow node changed. Try again.")
            result, _ = document.record_command("workflowNode", "user", mutate, node_ids=(node_id,) if node_id else ())
            await publish_scene()
            return result if result is not None else True
        except (ValueError, SceneError) as exc:
            raise IntentValidationError([scrub_text(str(exc))]) from exc
        except (KeyError, TypeError, AttributeError, StopIteration) as exc:
            raise IntentValidationError(["Invalid Workflow node request."]) from exc

    bus.register_intent("scene", "workflowLibrary", library_action, args_schema=WorkflowArgs)
    bus.register_intent("scene", "mutateWorkflowNode", edit_node, args_schema=WorkflowArgs)
    return resolve
