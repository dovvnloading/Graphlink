"""Typed bridge to Graph Runner's deterministic note adapter and result saving."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from backend.canvas import SceneDocument
from .adapter import StepContext
from .artifacts import WorkflowArtifacts, digest
from .contracts import FileValue, ImageValue, JsonValue, PortSet, PortSpec, TableValue, TextValue, WorkflowStepSpec
from .planning import StepPolicy
from .ports import coerce_port
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


class GraphGroupSumAdapter:
    adapter_id = "graph.group_sum"
    adapter_version = "1"
    side_effect_class = "pure"

    def ports(self, config_json: str) -> PortSet:
        config = json.loads(config_json)
        if (set(config) != {"label_column", "value_column", "output_kind"}
                or config["output_kind"] not in ("table", "chart", "artifact")
                or not all(isinstance(config[key], str) and config[key]
                           for key in ("label_column", "value_column"))):
            raise ValueError("Choose grouping and numeric columns and an output kind.")
        kind = {"table": "table", "chart": "json", "artifact": "text"}[config["output_kind"]]
        return PortSet((PortSpec("table", "table", True),), (PortSpec("result", kind, True),))

    def prepare(self, spec: WorkflowStepSpec, inputs):
        self.ports(spec.config_json)
        if set(inputs) != {"table"} or not isinstance(inputs["table"], TableValue):
            raise ValueError("Group sum requires a table.")
        csv_text = coerce_port(inputs["table"], PortSpec("table", "text", True)).text
        return spec.config_json, csv_text

    def fingerprint(self, prepared):
        return digest([self.adapter_id, self.adapter_version, *prepared])

    def _evaluate(self, prepared, document):
        from .graph_steps import adapter_for_step
        from backend.domain.table_workflows import table_output
        config_json, csv_text = prepared
        config = json.loads(config_json)
        kind = config["output_kind"]
        source = document.add_table(0, 0, csv_text)
        target = None
        try:
            target = document.create_table_output(source.id, kind, config["label_column"], config["value_column"])
            step = {"kind": kind, "sourceId": source.id, "nodeId": target.id, "operation": "group_sum"}
            adapter_for_step(step).evaluate(document, step)
            binding = table_output(target)
            if binding is None:
                raise ValueError("The grouped result is missing.")
            result = binding.current.result
            if kind == "chart":
                return JsonValue(result)
            if kind == "artifact":
                return TextValue(result)
            columns, *rows = csv.reader(io.StringIO(result))
            return TableValue(tuple(columns), tuple(tuple(row) for row in rows))
        finally:
            document.remove_nodes([source.id] if target is None else [source.id, target.id])

    def preview(self, prepared):
        value = self._evaluate(prepared, SceneDocument())
        return {"result": {"kind": value.kind}}

    async def run(self, prepared, context: StepContext):
        if context.scratch_document is None:
            raise ValueError("Group sum requires a scratch document.")
        return {"result": self._evaluate(prepared, context.scratch_document)}

    async def cancel(self, context: StepContext) -> None:
        pass


class GraphLocalModelAdapter:
    """The existing local Graph Runner model binding, on a scratch document.

    No canvas or legacy graph receipt is written; the detached journal owns
    dispatch and results. Remote models are not disguised as local models.
    """
    adapter_id = "graph.local_model"
    adapter_version = "1"
    side_effect_class = "executes"

    def ports(self, config_json: str) -> PortSet:
        from backend.domain.graph_model_binding import validate_local_model_identity
        config = json.loads(config_json)
        if set(config) != {"provider", "model", "endpoint"} or not isinstance(config["endpoint"], str):
            raise ValueError("Pin the local provider, model and endpoint.")
        validate_local_model_identity(config["provider"], config["model"])
        port = PortSpec("text", "text", True)
        return PortSet((port,), (port,))

    def execution_policy(self, spec: WorkflowStepSpec) -> StepPolicy:
        from backend.api.intents_graph_model import _require_loopback_ollama
        config = json.loads(spec.config_json)
        self.ports(spec.config_json)
        if config["endpoint"] != _require_loopback_ollama():
            raise ValueError("The local model endpoint changed; preview it again.")
        return StepPolicy("local", (config["provider"], config["model"]), 0.0)

    def prepare(self, spec: WorkflowStepSpec, inputs):
        self.ports(spec.config_json)
        if set(inputs) != {"text"} or not isinstance(inputs["text"], TextValue):
            raise ValueError("Local model requires text.")
        text = inputs["text"].text
        if not text.strip() or len(text.encode("utf-8")) > 65536:
            raise ValueError("Model input must contain text and be at most 64 KiB.")
        return spec.config_json, text

    def fingerprint(self, prepared):
        return digest([self.adapter_id, self.adapter_version, *prepared])

    def preview(self, prepared):
        return {"text": {"kind": "text"}}

    async def run(self, prepared, context: StepContext):
        from backend.api.intents_graph_model import _require_loopback_ollama, _response_text
        from backend.providers.graph_local_ollama import async_local_ollama_complete
        from .graph_steps import adapter_for_step
        config_json, text = prepared
        config = json.loads(config_json)
        if (context.model_identity != (config["provider"], config["model"])
                or config["endpoint"] != _require_loopback_ollama()):
            raise PermissionError("The approved local model identity or endpoint changed.")
        document = context.scratch_document
        if document is None:
            raise ValueError("Local model requires a scratch document.")
        source = document.add_note(0, 0)
        target = None
        try:
            document.set_note_content(source.id, text)
            target = document.add_model_node(source.id, config["provider"], config["model"])
            step = {"kind": "model", "sourceId": source.id, "nodeId": target.id, "operation": "model"}
            adapter = adapter_for_step(step)
            adapter.pin(document, step)
            adapter.inspect(document, step)
            payload = await async_local_ollama_complete(
                prompt=text, model_id=config["model"], endpoint=config["endpoint"],
            )
            return {"text": TextValue(_response_text(payload))}
        finally:
            document.remove_nodes([source.id] if target is None else [source.id, target.id])

    async def cancel(self, context: StepContext) -> None:
        # Cancellation of the awaited HTTP task closes its stream; no retry.
        pass


def detached_registry(registry: StepAdapterRegistry) -> StepAdapterRegistry:
    return registry.with_entries((
        ("graph.note", "1", GraphNoteAdapter()),
        ("graph.group_sum", "1", GraphGroupSumAdapter()),
        ("graph.local_model", "1", GraphLocalModelAdapter()),
    ))


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
        if payload.get("asset_ref") and payload["kind"] not in ("file", "image"):
            document.add_document_node(
                0, y, f"{name}.json", f"Workflow output: {name}", "document", None,
                file_path=f'asset://{payload["asset_ref"]}', mime_type=payload["mime_type"],
                byte_size=payload["size_bytes"],
            )
            continue
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
