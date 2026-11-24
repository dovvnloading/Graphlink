"""Task-level evaluations of the detached workflow engine.

Run offline: python -m backend.evals.workflow_tasks
Compare local models explicitly: python -m backend.evals.workflow_tasks --live --model qwen3:8b
Only the provider-facing host is scripted; typed adapters, bindings, previews,
approvals, scheduling, persistence and pure transformations are production code.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.chat_library import get_default_workspace_id
from backend.workflow.contracts import (
    InputBinding, JsonValue, PortSpec, StepOutputSource, TableValue, TextValue,
    WorkflowInputSource, WorkflowOutput, WorkflowSpec, WorkflowStepSpec, freeze_config,
)
from backend.workflow.engine import RunLimits, WorkflowEngine
from backend.workflow.planning import StepPolicy
from backend.workflow.registry import builtin_registry
from backend.workflow.step_adapters import model_messages
from graphlink_task_config import LOCAL_PROVIDER_OLLAMA

TASKS = ("research-summary", "table-chart-report", "conditional-repair", "mapped-summaries")
RESEARCH_QUERY = "What did the two fictional battery trials find?"
EVIDENCE = "[A] Trial A: 6 hours.\n[B] Trial B: 8 hours."
RESEARCH_ANSWER = "Trial A lasted 6 hours [A]; Trial B lasted 8 hours [B]."
CHART_ANSWER = "West total 5; East total 4."
DOCUMENTS = ("Alpha: 3 trees planted.", "Beta: 5 trees planted.", "Gamma: 2 trees planted.")
MAP_ANSWERS = ("Alpha: 3", "Beta: 5", "Gamma: 2")
MAX_REPAIRS = 2
SYSTEM = "Use only the supplied fictional data. Follow the requested output format exactly."
MODEL_PRICE = 0.02
RESEARCH_PRICE = 0.01


@dataclass(frozen=True)
class ModelIdentity:
    model_id: str
    version: str
    price_usd_per_call: float
    mode: str
    endpoint: str | None = None


SCRIPTED = ModelIdentity("workflow-fixture:v1", "1", MODEL_PRICE, "scripted")


@dataclass(frozen=True)
class TaskResult:
    task: str
    success: bool
    step_count: int
    cost_usd: float
    retries: int
    wrong_side_effect_count: int
    run_statuses: tuple[str, ...]
    effect_count: int


def _effect(adapter, step, attempt=1):
    return adapter, step, attempt


def wrong_effect_count(observed, expected):
    """Unexpected destinations/steps, duplicate calls and extra attempts count."""
    return sum((Counter(observed) - Counter(expected)).values())


def _check_chart(value):
    return (isinstance(value, dict) and value.get("type") == "bar"
            and value.get("labels") == ["West", "East"] and value.get("values") == [5, 4])


class EvaluationHost:
    """Restricted provider boundary, never a general-purpose action executor.

    Log dispatch attempts independently of the workflow receipt, including
    forbidden requests. No file, code, agent, MCP or real research tool can run.
    """

    def __init__(self, model, overrides=None):
        self.identity = model
        self.overrides = dict(overrides or {})
        self.effects = []
        self.wrong_inputs = 0

    @property
    def research_price(self):
        return RESEARCH_PRICE if self.identity.mode == "scripted" else 0.0

    def policy(self, step):
        if step.adapter.adapter_id == "model.generate":
            return StepPolicy("cloud", (LOCAL_PROVIDER_OLLAMA, self.identity.model_id),
                              self.identity.price_usd_per_call)
        if step.adapter.adapter_id == "web.research":
            return StepPolicy("cloud", ("fixture", "research-corpus-v1"), self.research_price)
        if step.adapter.side_effect_class == "cloud":
            # Unknown cloud calls can be observed/denied, never dispatched.
            return StepPolicy("cloud", ("fixture", "forbidden"), 0.0)
        return StepPolicy()

    def _expect(self, condition):
        if not condition:
            self.wrong_inputs += 1
            raise ValueError("Evaluation dispatched an unexpected request")

    def _script(self, step_id, subject, messages):
        self._expect(messages[0] == {"role": "system", "content": SYSTEM})
        if step_id == "summary":
            self._expect(subject == EVIDENCE)
            self._expect(messages[1]["content"] ==
                         "Summarize both trials in one sentence with [A] and [B] citations:\n" + EVIDENCE)
            return RESEARCH_ANSWER
        if step_id == "chart-summary":
            self._expect(_check_chart(json.loads(subject)))
            self._expect(messages[1]["content"] ==
                         "Report both region totals exactly as 'West total N; East total N.'\n" + subject)
            return CHART_ANSWER
        if step_id == "draft":
            self._expect(subject == "3 + 4")
            self._expect(messages[1]["content"] == "Return only the integer result of 3 + 4.")
            return "9"  # deliberate semantic failure, not a transport retry
        if step_id.startswith("repair-"):
            self._expect(subject.strip() != "7")
            self._expect(messages[1]["content"] ==
                         f"Correct this wrong answer to 3 + 4: {subject}. Return only the integer.")
            return "7"
        if step_id.startswith("map-"):
            index = int(step_id.removeprefix("map-"))
            self._expect(0 <= index < len(DOCUMENTS) and subject == DOCUMENTS[index])
            self._expect(messages[1]["content"] == "Summarize the document as 'Name: N' only.\n" + subject)
            return MAP_ANSWERS[index]
        self._expect(False)

    async def run(self, adapter, prepared, context):
        adapter_id, step_id = adapter.adapter_id, context.step_id
        self.effects.append(_effect(adapter_id, step_id, context.attempt))
        inputs = prepared.inputs()
        if adapter_id == "web.research" and step_id == "research":
            self._expect(inputs["query"] == TextValue(RESEARCH_QUERY))
            context.report_cost(self.research_price)
            return {"text": TextValue(EVIDENCE), "sources": JsonValue(["A", "B"])}
        if adapter_id != "model.generate":
            raise PermissionError("The evaluation host has no external action tools")
        self._expect(context.model_identity == (LOCAL_PROVIDER_OLLAMA, self.identity.model_id))
        cfg = prepared.config()
        self._expect(cfg["model_id"] == self.identity.model_id and cfg["provider"] == LOCAL_PROVIDER_OLLAMA)
        messages = model_messages(cfg, inputs)
        if self.identity.mode == "scripted":
            reply = self._script(step_id, inputs["subject"].text, messages)
            reply = self.overrides.get(step_id, reply)
        else:
            _require_live_opt_in()
            from backend.api.intents_graph_model import _require_loopback_ollama
            from backend.providers.graph_local_ollama import async_local_ollama_complete
            self._expect(self.identity.endpoint == _require_loopback_ollama())
            payload = await async_local_ollama_complete(
                prompt=messages[0]["content"] + "\n\n" + messages[1]["content"],
                model_id=self.identity.model_id, endpoint=self.identity.endpoint,
            )
            reply = payload["message"]["content"]
        context.report_cost(self.identity.price_usd_per_call)
        return {"text": TextValue(reply)}

    async def cancel(self, context):
        pass


class TaskRun:
    def __init__(self, path, task, model, overrides):
        self.task = task
        self.host = EvaluationHost(model, overrides)
        self.timeout = 20 if model.mode == "scripted" else 300
        self.engine = WorkflowEngine(path, registry=builtin_registry(self.host), policy_for=self.host.policy,
                                     limits=RunLimits(cloud_concurrency=1, wall_time_seconds=self.timeout))
        self.ws = get_default_workspace_id(path)
        self.records = []
        self.expected = []
        self.retries = 0

    def step(self, step_id, adapter, config, **sources):
        config_json = freeze_config(config)
        descriptor = self.engine.registry.describe(adapter, "1", config_json)
        return WorkflowStepSpec(step_id, descriptor, config_json,
                                tuple(InputBinding(name, source) for name, source in sources.items()))

    def model_step(self, step_id, template, source):
        self.expected.append(_effect("model.generate", step_id))
        return self.step(step_id, "model.generate", {
            "provider": LOCAL_PROVIDER_OLLAMA, "model_id": self.host.identity.model_id,
            "system_prompt": SYSTEM, "template": template, "output_kind": "text",
        }, subject=source)

    async def run(self, steps, parameters, outputs):
        spec = WorkflowSpec(
            1, self.task, str(len(self.records) + 1), self.task,
            tuple(PortSpec(key, value.kind, True) for key, value in parameters.items()), tuple(steps),
            tuple(WorkflowOutput(PortSpec(name, kind, True), source) for name, kind, source in outputs),
        )
        preview = self.engine.preview(spec, parameters)
        token = self.engine.approve(self.ws, preview) if preview.requires_approval else None
        run_id = self.engine.start(self.ws, spec, parameters, approval_token=token)
        await asyncio.wait_for(self.engine.wait(self.ws, run_id), timeout=self.timeout + 5)
        record = self.engine.store.read(self.ws, run_id)
        self.records.append(record)
        return {name: self.engine.artifacts.unpack(value) for name, value in record["outputs"].items()}

    def result(self, score):
        observed = self.host.effects
        wrong = wrong_effect_count(observed, self.expected) + self.host.wrong_inputs
        statuses = tuple(record["status"] for record in self.records)
        started = [event for record in self.records for event in record["events"]
                   if event["kind"] == "step-started"]
        cost = sum((Decimal(str(record["cost_usd"])) for record in self.records), Decimal(0))
        return TaskResult(
            self.task, bool(score and statuses and all(s == "completed" for s in statuses)
                            and Counter(observed) == Counter(self.expected) and wrong == 0),
            len(started), float(cost), self.retries + sum(event["attempt"] > 1 for event in started),
            wrong, statuses, len(observed),
        )


async def _research(task):
    task.expected.append(_effect("web.research", "research"))
    research = task.step("research", "web.research", {"max_sources": 2},
                         query=WorkflowInputSource("query"))
    summary = task.model_step("summary", "Summarize both trials in one sentence with [A] and [B] citations:\n{subject}",
                         StepOutputSource("research", "text"))
    result = await task.run([summary, research], {"query": TextValue(RESEARCH_QUERY)}, [
        ("summary", "text", StepOutputSource("summary", "text")),
        ("sources", "json", StepOutputSource("research", "sources")),
    ])
    text = result.get("summary", TextValue("")).text.strip()
    return text == RESEARCH_ANSWER and result.get("sources") == JsonValue(["A", "B"])


async def _table(task):
    table = TableValue(("Region", "Qty"), (("West", 2), ("West", 3), ("East", 4)))
    cfg = {"label_column": "Region", "value_column": "Qty"}
    chart = task.step("chart", "chart.generate", cfg, table=WorkflowInputSource("table"))
    intro = task.model_step("chart-summary", "Report both region totals exactly as 'West total N; East total N.'\n{subject}",
                       StepOutputSource("chart", "chart"))
    report = task.step("report", "report.generate", cfg, table=WorkflowInputSource("table"),
                       text=StepOutputSource("chart-summary", "text"))
    result = await task.run([report, intro, chart], {"table": table}, [
        ("chart", "json", StepOutputSource("chart", "chart")),
        ("report", "text", StepOutputSource("report", "report")),
    ])
    chart_value = result.get("chart", JsonValue(None)).value
    report_text = result.get("report", TextValue("")).text
    # The production report escapes CSV labels as padded Markdown code spans.
    # Check complete rows, not substrings or the model-generated introduction.
    report_lines = report_text.splitlines()
    return (_check_chart(chart_value) and report_text.startswith(CHART_ANSWER + "\n\n")
            and "| ` West ` | 5 |" in report_lines and "| ` East ` | 4 |" in report_lines)


async def _repair(task):
    # Main's WorkflowSpec is a DAG: the host admits a new frozen repair
    # revision only if the deterministic validator rejects the prior output.
    draft = task.model_step("draft", "Return only the integer result of {subject}.", WorkflowInputSource("problem"))
    result = await task.run([draft], {"problem": TextValue("3 + 4")}, [
        ("candidate", "text", StepOutputSource("draft", "text")),
    ])
    candidate = result.get("candidate")
    for index in range(MAX_REPAIRS):
        if candidate is None or candidate.text.strip() == "7":
            break
        task.retries += 1
        step_id = f"repair-{index + 1}"
        repair = task.model_step(step_id, "Correct this wrong answer to 3 + 4: {subject}. Return only the integer.",
                            WorkflowInputSource("candidate"))
        result = await task.run([repair], {"candidate": candidate}, [
            ("candidate", "text", StepOutputSource(step_id, "text")),
        ])
        candidate = result.get("candidate")
    return candidate is not None and candidate.text.strip() == "7"


async def _mapped(task):
    steps = [task.model_step(f"map-{i}", "Summarize the document as 'Name: N' only.\n{subject}",
                        WorkflowInputSource(f"doc{i}")) for i in range(len(DOCUMENTS))]
    join = task.step("join", "transform.template", {"template": "{a}\n{b}\n{c}"},
                     a=StepOutputSource("map-0", "text"), b=StepOutputSource("map-1", "text"),
                     c=StepOutputSource("map-2", "text"))
    result = await task.run([join, *reversed(steps)],
                            {f"doc{i}": TextValue(text) for i, text in enumerate(DOCUMENTS)}, [
                                ("summary", "text", StepOutputSource("join", "text")),
                            ])
    return result.get("summary") == TextValue("\n".join(MAP_ANSWERS))


_SCENARIOS = dict(zip(TASKS, (_research, _table, _repair, _mapped), strict=True))


async def evaluate_task(path: Path, name: str, *, model=SCRIPTED, overrides=None) -> TaskResult:
    if model.mode != "scripted":
        _require_live_opt_in()
    task = TaskRun(path, name, model, overrides)
    try:
        return task.result(await _SCENARIOS[name](task))
    finally:
        await task.engine.shutdown()


async def evaluate_model(model=SCRIPTED):
    with TemporaryDirectory(prefix="graphlink-workflow-eval-") as root:
        return [await evaluate_task(Path(root) / f"{name}.db", name, model=model) for name in TASKS]


def _require_live_opt_in():
    if "CI" in os.environ or "GITHUB_ACTIONS" in os.environ:
        raise RuntimeError("Live workflow comparisons are disabled in CI.")


async def _installed_model(model):
    """Resolve the exact local tag to its content version; no ambient auth/proxy."""
    _require_live_opt_in()
    import httpx
    from backend.api.intents_graph_model import _require_loopback_ollama
    from backend.domain.graph_model_binding import validate_local_model_identity

    validate_local_model_identity(LOCAL_PROVIDER_OLLAMA, model)
    endpoint = _require_loopback_ollama()
    async with httpx.AsyncClient(base_url=endpoint, timeout=10, trust_env=False, follow_redirects=False) as client:
        response = await client.get("/api/tags")
        response.raise_for_status()
        matches = [item for item in response.json()["models"] if item.get("name") == model]
    if len(matches) != 1:
        raise ValueError("Select an exact installed model tag (including its version tag).")
    if matches[0].get("remote_host") or matches[0].get("remote_model"):
        raise ValueError("Cloud-backed Ollama models are not eligible for the local comparison.")
    version = matches[0].get("digest")
    if not isinstance(version, str) or not re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", version):
        raise ValueError("Installed model did not expose a content digest.")
    return ModelIdentity(model, version, 0.0, "live-local", endpoint)


def _report(model, results):
    return {
        "provider": LOCAL_PROVIDER_OLLAMA if model.mode == "live-local" else "scripted",
        "model": model.model_id, "version": model.version, "mode": model.mode,
        "price": {"currency": "USD", "model_per_call": model.price_usd_per_call,
                  "research_per_call": RESEARCH_PRICE if model.mode == "scripted" else 0.0,
                  "basis": "synthetic fixture charges" if model.mode == "scripted"
                  else "local API billing only; excludes hardware and electricity"},
        "tasks": [asdict(result) for result in results],
    }


async def comparison(model_names=()):
    if model_names:
        _require_live_opt_in()
    offline = await evaluate_model()
    reports = [_report(SCRIPTED, offline)]
    for name in model_names:
        model = await _installed_model(name)
        results = await evaluate_model(model)
        if await _installed_model(name) != model:
            raise RuntimeError("The local model version or endpoint changed during the evaluation.")
        reports.append(_report(model, results))
    return reports


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Opt in to loopback Ollama calls")
    parser.add_argument("--model", dest="model_names", action="append", default=[],
                        help="Exact installed model tag; repeat to compare")
    args = parser.parse_args(argv)
    if bool(args.model_names) != args.live:
        parser.error("Use --live together with at least one --model; omit both for offline mode.")
    try:
        reports = asyncio.run(comparison(args.model_names))
    except Exception as exc:
        parser.exit(2, f"Workflow comparison failed: {exc}\n")
    sys.stdout.write(json.dumps(reports, indent=2, sort_keys=True) + "\n")
    # Only the deterministic baseline is a regression gate. Live scores are
    # comparative measurements, not promises of model quality.
    return 0 if all(task["success"] for task in reports[0]["tasks"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
