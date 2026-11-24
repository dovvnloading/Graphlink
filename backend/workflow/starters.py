"""Versioned, self-contained examples for the detached Workflow Engine.

The catalog contains synthetic inputs only. Importing a starter saves a
definition, not an approval or a run. Keep v1 definitions immutable: publish
a new starter ID for changes to an existing example.
"""
from __future__ import annotations

from dataclasses import asdict

from graphlink_task_config import LOCAL_PROVIDER_OLLAMA

from .contracts import (
    InputBinding, PortSpec, StepOutputSource, TableValue, TextValue,
    WorkflowInputSource, WorkflowOutput, WorkflowSpec, WorkflowStepSpec, freeze_config,
)
from .library import _json, _name, _safe, canonical
from .registry import builtin_registry
from .scratch_adapters import detached_registry

STARTER_FORMAT = "graphlink-starter-workflow"
MODEL_A = "qwen3:8b"
MODEL_B = "llama3.2:3b"
ENDPOINT = "http://127.0.0.1:11434"


def _port(name, kind="text"):
    return PortSpec(name, kind, True)


def _input(name):
    return WorkflowInputSource(name)


def _source(step, port="text"):
    return StepOutputSource(step, port)


def _output(name, step, port="text", kind="text"):
    return WorkflowOutput(_port(name, kind), _source(step, port))


class _Builder:
    def __init__(self, starter_id, name):
        self.starter_id = starter_id
        self.name = name
        self.registry = detached_registry(builtin_registry())
        self.steps = []

    def step(self, step_id, adapter, config, **bindings):
        frozen = freeze_config(config)
        descriptor = self.registry.describe(adapter, "1", frozen)
        self.steps.append(WorkflowStepSpec(
            step_id, descriptor, frozen,
            tuple(InputBinding(port, source) for port, source in bindings.items()),
        ))

    def template(self, step_id, template, **bindings):
        self.step(step_id, "transform.template", {"template": template}, **bindings)

    def model(self, step_id, source, model=MODEL_A):
        self.step(step_id, "graph.local_model",
                  {"provider": LOCAL_PROVIDER_OLLAMA, "model": model, "endpoint": ENDPOINT},
                  text=source)

    def spec(self, inputs, outputs):
        return WorkflowSpec(
            1, self.starter_id, "v1", self.name, tuple(inputs), tuple(self.steps), tuple(outputs),
        )

    def document(self, inputs, outputs, **examples):
        return _json({
            "format": STARTER_FORMAT, "starter_id": self.starter_id,
            "spec": asdict(self.spec(inputs, outputs)), "parameters": [],
            "example_inputs": {name: asdict(value) for name, value in examples.items()},
        })


def _table_chart_report(b):
    grouping = {"label_column": "team", "value_column": "tickets"}
    b.step("chart", "chart.generate", grouping, table=_input("table"))
    b.template("interpret_chart",
               "Explain this chart in two sentences using its actual values.\nChart:\n{chart}",
               chart=_source("chart", "chart"))
    b.model("commentary", _source("interpret_chart"))
    b.step("report", "report.generate", grouping, table=_input("table"), text=_source("commentary"))
    return b.document(
        [_port("table", "table")],
        [_output("chart", "chart", "chart", "json"), _output("report", "report", "report")],
        table=TableValue(("team", "tickets"), (("Support", 8), ("Sales", 3), ("Support", 4))),
    )


def _research_summary(b):
    b.template("research_prompt",
               "Extract evidence relevant to the question from the supplied research notes. "
               "Keep source labels and distinguish evidence from uncertainty.\n"
               "Question: {question}\nResearch notes:\n{sources}",
               question=_input("question"), sources=_input("sources"))
    b.model("findings", _source("research_prompt"))
    b.template("summary_prompt",
               "Summarize these research findings in three concise bullets. Preserve source labels "
               "and do not introduce new claims.\nFindings:\n{findings}",
               findings=_source("findings"))
    b.model("summary", _source("summary_prompt"))
    return b.document(
        [_port("question"), _port("sources")],
        [_output("findings", "findings"), _output("summary", "summary")],
        question=TextValue("Did the pilot reduce support wait times?"),
        sources=TextValue("[R1] Pilot log: median wait fell from 12 to 8 minutes.\n"
                          "[R2] Review notes: only one team participated; no control group."),
    )


def _compare_models(b):
    b.model("model_a", _input("prompt"), MODEL_A)
    b.model("model_b", _input("prompt"), MODEL_B)
    b.template("comparison", "## Model A\n{a}\n\n## Model B\n{b}",
               a=_source("model_a"), b=_source("model_b"))
    return b.document(
        [_port("prompt")],
        [_output("model_a", "model_a"), _output("model_b", "model_b"),
         _output("comparison", "comparison")],
        prompt=TextValue("Explain why a reusable water bottle reduces waste in one sentence."),
    )


def _document_qa(b):
    b.template("grounded_prompt",
               "Answer the question using only the supplied document passages. Cite each factual "
               "claim with its passage ID (for example [D1]). If the answer is absent, say so; "
               "do not invent citations.\nQuestion: {question}\nDocument passages:\n{document}",
               question=_input("question"), document=_input("document"))
    b.model("answer", _source("grounded_prompt"))
    return b.document(
        [_port("question"), _port("document")], [_output("answer", "answer")],
        question=TextValue("How long may a loan be renewed?"),
        document=TextValue("[D1] Library handbook, page 2: Loans last 14 days.\n"
                           "[D2] Library handbook, page 3: A loan may be renewed once for 7 days."),
    )


def _summarize_rows(b):
    child = _Builder("starter-row-summary-child-v1", "Summarize one row")
    child.template("row_prompt", "Summarize this table row in one sentence:\n{row}",
                   row=_input("item"))
    child.model("summary", _source("row_prompt"))
    child_spec = child.spec([_port("item", "json")], [_output("summary", "summary")])
    b.step("summarize_rows", "control.map",
           {"workflow": _json(asdict(child_spec)), "concurrency": 2, "on_error": "stop"},
           items=_input("table"))
    return b.document(
        [_port("table", "table")], [_output("rows", "summarize_rows", "results", "json")],
        table=TableValue(("topic", "detail"), (("Delivery", "Two days early"),
                                              ("Packaging", "Recyclable cardboard"),
                                              ("Support", "Resolved on first contact"))),
    )


def _quality_gate(b):
    b.template("rewrite_prompt",
               "Rewrite the draft as two lines: Summary: <summary> and Action: <next step>.\n"
               "Draft:\n{draft}", draft=_input("draft"))
    b.model("rewrite", _source("rewrite_prompt"))
    b.step("quality_gate", "control.assert",
           {"kind": "text", "predicate": "regex",
            "pattern": r"(?m)^Action:[ \t]+\S", "on_failure": "block"},
           value=_source("rewrite"))
    b.template("publishable", "{text}", text=_source("quality_gate", "pass"))
    return b.document(
        [_port("draft")], [_output("approved_text", "publishable")],
        draft=TextValue("The launch checklist is complete. Ask the team to review it tomorrow."),
    )


# IDs include the version because imported library revisions must remain valid
# after adding a new version of a built-in starter.
_CATALOG = {
    "table-chart-report-v1": (
        "Table to chart to report", "Chart grouped table values, interpret the chart and generate a report.",
        _table_chart_report,
    ),
    "research-summary-v1": (
        "Research to summary", "Extract findings from supplied source notes and write a cited summary.",
        _research_summary,
    ),
    "compare-models-v1": (
        "Compare two models on one prompt", "Send the same prompt to two pinned local models and compare their answers.",
        _compare_models,
    ),
    "document-qa-v1": (
        "Document question answering with citations", "Answer from supplied, passage-labelled document text.",
        _document_qa,
    ),
    "summarize-rows-v1": (
        "Summarize every table row", "Map a contained model workflow over the table; retain one result per row.",
        _summarize_rows,
    ),
    "text-quality-gate-v1": (
        "Text quality gate", "Rewrite a draft and use Assert to block output without an Action line.",
        _quality_gate,
    ),
}


def list_starters():
    return [{"id": key, "name": name, "description": description}
            for key, (name, description, _) in _CATALOG.items()]


def starter_document(starter_id):
    if not isinstance(starter_id, str) or starter_id not in _CATALOG:
        raise ValueError("Unknown built-in workflow starter.")
    name, _, build = _CATALOG[starter_id]
    return build(_Builder(starter_id, name))


def validate_starter_document(document):
    """Only admit exact shipped starter snapshots, with local library identity.

    The separate format never widens the pure canvas document validator.
    Example inputs are fixed public synthetic values, not captured user data.
    """
    if not isinstance(document, dict):
        raise ValueError("Invalid built-in workflow document.")
    expected = starter_document(document.get("starter_id"))
    spec = document.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("Invalid built-in workflow definition.")
    for field in ("workflow_id", "revision", "name"):
        value = _name(spec.get(field))
        _safe(value)
        expected["spec"][field] = value
    if canonical(document) != canonical(expected):
        raise ValueError("A built-in workflow must match its published version. "
                         "Edit the example run request instead.")
    return expected
