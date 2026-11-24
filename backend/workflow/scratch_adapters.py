"""Typed bridge to Graph Runner's deterministic note adapter and result saving."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.canvas import SceneDocument
from .adapter import StepContext
from .artifacts import WorkflowArtifacts, digest
from .contracts import FileValue, ImageValue, PortSet, PortSpec, TextValue, WorkflowStepSpec
from .registry import StepAdapterRegistry


class GraphNoteAdapter:
    adapter_id = "graph.note"
    adapter_version = "1"
    side_effect_class = "pure"

    def ports(self, config_json: str) -> PortSet:
        config = json.loads(config_json)
        if set(config) != {"operation"} or config["operation"] not in ("copy", "word_count"):
            raise ValueError("Choose Copy text or Word count.")
        port = PortSpec("text", "text", True)
        return PortSet((port,), (port,))

    def prepare(self, spec: WorkflowStepSpec, inputs):
        self.ports(spec.config_json)
        if set(inputs) != {"text"} or not isinstance(inputs["text"], TextValue):
            raise ValueError("Graph note requires text.")
        return json.loads(spec.config_json)["operation"], inputs["text"].text

    def fingerprint(self, prepared):
        return digest([self.adapter_id, self.adapter_version, *prepared])

    def _evaluate(self, prepared, document):
        from .graph_steps import adapter_for_step
        operation, text = prepared
        source = document.add_note(0, 0)
        target = document.add_note(400, 0)
        try:
            document.set_note_content(source.id, text)
            document.bind_note_text_input(source.id, target.id, operation)
            step = {"kind": "note", "sourceId": source.id, "nodeId": target.id, "operation": operation}
            adapter_for_step(step).evaluate(document, step)
            return document.nodes[target.id].content
        finally:
            document.remove_nodes([source.id, target.id])

    def preview(self, prepared):
        return {"text": {"characters": len(self._evaluate(prepared, SceneDocument()))}}

    async def run(self, prepared, context: StepContext):
        if context.scratch_document is None:
            raise ValueError("Graph note requires a scratch document.")
        return {"text": TextValue(self._evaluate(prepared, context.scratch_document))}

    async def cancel(self, context: StepContext) -> None:
        pass


def detached_registry(registry: StepAdapterRegistry) -> StepAdapterRegistry:
    return registry.with_entries((("graph.note", "1", GraphNoteAdapter()),))


def save_result_graph(db_path: Path, artifacts: WorkflowArtifacts, workspace_id: int,
                      run: dict[str, Any], *, title: str | None = None) -> int:
    from backend.chat_library import save_chat_atomically_row
    from backend.session_save import build_chat_data

    document = SceneDocument()
    results = dict(run["outputs"])
    if not results:
        for step_id, step in run["steps"].items():
            if step["status"] == "completed":
                results.update({f"{step_id}.{name}": value for name, value in step["outputs"].items()})
    if not results:
        raise ValueError("This workflow has no completed results to save.")
    for index, (name, payload) in enumerate(results.items()):
        y = index * 320
        value = artifacts.unpack(payload)
        if isinstance(value, ImageValue):
            document.add_image_node(0, y, artifacts.bytes_for(value.asset), name, None,
                                    mime_type=value.asset.media_type)
        elif isinstance(value, FileValue):
            document.add_document_node(
                0, y, value.asset.filename, f"Workflow output: {name}", "document", None,
                file_path=f"asset://{value.asset.sha256}", mime_type=value.asset.media_type,
                byte_size=value.asset.size_bytes,
            )
        else:
            note = document.add_note(0, y)
            if isinstance(value, TextValue):
                text = value.text
            elif value.kind == "json":
                text = json.dumps(value.value, ensure_ascii=False, indent=2)
            else:
                text = json.dumps({"columns": value.columns, "rows": value.rows}, ensure_ascii=False, indent=2)
            document.set_note_content(note.id, f"{name}\n\n{text}")
    data = build_chat_data(document, asset_store=artifacts.store)
    notes = data.pop("notes_data", [])
    pins = data.pop("pins_data", [])
    graph_id, _ = save_chat_atomically_row(
        db_path, None, title or f'{run["spec"]["name"]} result', data, notes, pins,
        workspace_id=workspace_id,
    )
    return graph_id
