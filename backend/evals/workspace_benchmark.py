"""Opt-in, live workspace-agent benchmark. Never imported by the CI eval runner.

Run explicitly with python -m backend.evals.workspace_benchmark --run.
Every case drives the production harness loop, real filesystem tools and a
selected provider/model, but only inside throwaway scratch directories.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


@dataclass(frozen=True)
class Case:
    name: str
    prompt: str
    inputs: dict[str, str]
    expected: dict[str, str | dict[str, int]]
    required_tools: tuple[tuple[str, int], ...]


# Pinned inputs, expected artifacts, and tool evidence. Never sample tasks or
# reuse a workspace: each run sees only the files declared in its case.
CASES = (
    Case(
        name="read_and_extract",
        prompt=(
            "Read data/shipments.csv. Which region has the most packages? "
            "Write only its lowercase region name to answer.txt."
        ),
        inputs={"data/shipments.csv": "region,packages\nnorth,13\nwest,27\neast,19\nsouth,8\n"},
        expected={"answer.txt": "west"},
        required_tools=(("fs.read", 1), ("fs.write", 1)),
    ),
    Case(
        name="combine_two_files",
        prompt=(
            "Read reports/january.csv and reports/february.csv. Sum tickets "
            "for each team across both months. Write totals.json as a JSON "
            "object mapping each team to its integer total, with no other keys."
        ),
        inputs={
            "reports/january.csv": "team,tickets\nlynx,8\norca,4\nheron,10\n",
            "reports/february.csv": "team,tickets\nlynx,11\norca,9\nheron,5\n",
        },
        expected={"totals.json": {"lynx": 19, "orca": 13, "heron": 15}},
        required_tools=(("fs.read", 2), ("fs.write", 1)),
    ),
    Case(
        name="precise_edit",
        prompt=(
            "Read config/runtime.ini, then use fs.edit to change only "
            "retry_limit=2 to retry_limit=5. Preserve every other byte "
            "of that file and do not rewrite it with fs.write."
        ),
        inputs={"config/runtime.ini": "[service]\nenabled=true\nretry_limit=2\ntimeout_ms=500\n"},
        expected={"config/runtime.ini": "[service]\nenabled=true\nretry_limit=5\ntimeout_ms=500\n"},
        required_tools=(("fs.read", 1), ("fs.edit", 1)),
    ),
    Case(
        name="search_and_deduplicate",
        prompt=(
            "Use fs.grep to find incident IDs on ERROR lines in logs/*.log "
            "(not .txt files). Write the distinct IDs, sorted alphabetically, "
            "one per line to incident_ids.txt. Do not include explanations."
        ),
        inputs={
            "logs/north.log": "INFO startup\nERROR id=INC-317 restart\nINFO id=INC-999 ignored\nERROR id=INC-204 timeout\n",
            "logs/south.log": "WARN id=INC-541 ignored\nERROR id=INC-204 retry\nERROR id=INC-317 restart\n",
            "logs/other.txt": "ERROR id=INC-900 out of scope\n",
        },
        expected={"incident_ids.txt": "INC-204\nINC-317"},
        required_tools=(("fs.grep", 1), ("fs.write", 1)),
    ),
)


@dataclass(frozen=True)
class Result:
    name: str
    passed: bool
    status: str
    detail: str
    seconds: float
    turns: int
    prompt_tokens: int
    completion_tokens: int
    provider_reported_turns: int
    metered_turns: int
    cost_usd: float | None
    tools: tuple[str, ...]


def grade(case: Case, workspace: Path, status: str, activity: list[dict]) -> tuple[bool, str]:
    """Score observed artifacts and successful real tools, never model claims."""
    if status != "done":
        return False, f"agent ended with status {status}"
    for tool, minimum in case.required_tools:
        count = sum(row.get("tool") == tool and row.get("outcome") == "ok" for row in activity)
        if count < minimum:
            return False, f"required {minimum} successful {tool} call(s), observed {count}"
    for relpath, expected in case.expected.items():
        path = (workspace / relpath).resolve()
        if not path.is_relative_to(workspace.resolve()) or not path.is_file():
            return False, f"missing or non-workspace artifact: {relpath}"
        try:
            actual = path.read_text(encoding="utf-8")
            if isinstance(expected, dict):
                if json.loads(actual) != expected:
                    return False, f"incorrect JSON artifact: {relpath}"
            elif relpath in case.inputs:
                # Editing an existing file must preserve its exact bytes.
                if actual != expected:
                    return False, f"unexpected changes in: {relpath}"
            elif actual.strip() != expected.strip():
                return False, f"incorrect text artifact: {relpath}"
        except (OSError, UnicodeError, ValueError) as exc:
            return False, f"invalid artifact {relpath}: {exc}"
    for relpath, original in case.inputs.items():
        if relpath in case.expected:
            continue
        try:
            if (workspace / relpath).read_text(encoding="utf-8") != original:
                return False, f"input file was modified: {relpath}"
        except (OSError, UnicodeError):
            return False, f"input file is missing or unreadable: {relpath}"
    return True, "expected files and required tool calls verified"


class UsageMeter:
    """Wrap only the real provider turn seam; estimate missing usage openly."""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.provider_reported_turns = 0
        self.metered_turns = 0
        self._lock = threading.Lock()

    @staticmethod
    def _count(text: str) -> int:
        from graphlink_token_estimator import TokenEstimator

        return TokenEstimator().count_tokens(text)

    def capture(self, actual_call, task, messages, tools=(), **kwargs):
        turn = actual_call(task, messages, tools, **kwargs)
        usage = turn.get("usage") or {}
        prompt = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        completion = usage.get("completion_tokens") if isinstance(usage, dict) else None
        prompt_real = isinstance(prompt, int) and not isinstance(prompt, bool) and prompt >= 0
        completion_real = isinstance(completion, int) and not isinstance(completion, bool) and completion >= 0
        if not prompt_real:
            # Includes tool declarations, not just conversation history.
            prompt = self._count(json.dumps({
                "messages": messages,
                "tools": [asdict(spec) for spec in tools],
            }, ensure_ascii=False, default=str))
        if not completion_real:
            completion = self._count(json.dumps({
                "message": turn.get("message", {}),
                "tool_calls": [call.history_entry() for call in turn.get("tool_calls", [])],
            }, ensure_ascii=False, default=str))
        with self._lock:
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            self.metered_turns += 1
            if prompt_real and completion_real:
                self.provider_reported_turns += 1
        return turn


def _runtime(provider: str, model: str, base_url: str | None, api_key: str = ""):
    import api_provider
    import graphlink_task_config as config
    from graphlink_cloud_providers import CLOUD_PROVIDERS
    from graphlink_model_catalog import ModelRef

    runtime = api_provider.ProviderRuntime()
    if provider == config.LOCAL_PROVIDER_OLLAMA:
        if base_url:
            raise ValueError("--base-url is only supported for cloud providers")
        runtime.initialize_local_provider(provider)
    elif provider == config.LOCAL_PROVIDER_LLAMACPP:
        if base_url:
            raise ValueError("--base-url is only supported for cloud providers")
        model_path = Path(model).expanduser().resolve()
        runtime.initialize_local_provider(provider, {"chat_model_path": str(model_path)})
        model = model_path.name  # ModelRef pins llama.cpp by configured basename.
    elif provider in CLOUD_PROVIDERS:
        runtime.initialize_api(provider, api_key, base_url, task_models={config.TASK_CHAT: model})
    else:
        raise ValueError(f"Unknown provider {provider!r}. Use Ollama, Llama.cpp, or a Graphlink cloud provider name.")
    return runtime, ModelRef(provider, model)


async def _drive(case: Case, runtime, model_ref, timeout: float, max_turns: int) -> Result:
    import api_provider
    from backend.domain.graph import SceneDocument
    from backend.domain.node_states import HarnessState
    from backend.harness.loop import run_harness
    from backend.harness.tools_fs import register_harness_fs_tools
    from backend.harness.workspace import ensure_workspace
    from backend.run_lifecycle import RunRegistry
    from backend.tools import ToolRegistry

    document = SceneDocument()
    node = document.add_harness_node(0, 0, case.prompt)
    state = node.state
    # The newly created harness node always owns this concrete state type.
    assert isinstance(state, HarnessState)
    state.harness_max_turns = max_turns
    workspace = ensure_workspace(state.harness_workspace_id)
    for relpath, content in case.inputs.items():
        path = workspace / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    registry = ToolRegistry()
    # Intentionally exclude shell, Python, graph, network, provider-spawn and
    # MCP tools. This is a filesystem capability benchmark, not arbitrary code.
    register_harness_fs_tools(registry)
    dispatcher = SimpleNamespace(_runs=RunRegistry())

    async def publish(_topic: str) -> None:
        return None

    bus = SimpleNamespace(publish=publish)
    cancel_event = threading.Event()
    handle = dispatcher._runs.claim("harness", node_id=node.id, cancel_event=cancel_event)
    meter = UsageMeter()
    provider_call = api_provider.chat_turn_with_tools
    started = time.monotonic()
    timeout_hit = False

    async def run() -> None:
        await run_harness(
            document=document, dispatcher=dispatcher, registry=registry,
            bus=bus, notifications=None, harness_node_id=node.id,
            user_text=case.prompt, request_id=handle.request_id,
            handle=handle, cancel_event=cancel_event,
            model_ref=model_ref, runtime=runtime,
        )

    try:
        # The patch only observes completed provider turns; it never replaces
        # model responses. Run sequentially in this standalone process.
        with mock.patch.object(
            api_provider, "chat_turn_with_tools",
            side_effect=lambda *a, **kw: meter.capture(provider_call, *a, **kw),
        ):
            pending = asyncio.create_task(run())
            try:
                deadline = asyncio.get_running_loop().time() + timeout
                while not pending.done():
                    if asyncio.get_running_loop().time() > deadline:
                        timeout_hit = True
                        cancel_event.set()
                        pending.cancel()
                        break
                    future = handle.approval_future
                    if future is not None and not future.done():
                        if state.harness_awaiting_approval:
                            # --run explicitly approves only fs.write/fs.edit
                            # in this newly created temporary workspace.
                            future.set_result(state.harness_approval_tool_name in {"fs.write", "fs.edit"})
                        elif state.harness_awaiting_question:
                            future.set_result(None)
                    await asyncio.sleep(0.02)
                outcomes = await asyncio.gather(pending, return_exceptions=True)
                if not timeout_hit and isinstance(outcomes[0], BaseException):
                    raise outcomes[0]
            finally:
                if not pending.done():
                    cancel_event.set()
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
    finally:
        dispatcher._runs.release(handle.request_id)
    elapsed = time.monotonic() - started
    status = "timeout" if timeout_hit else state.harness_status
    passed, detail = grade(case, workspace, status, state.harness_activity)
    if not passed and state.harness_status_detail:
        detail += f" ({state.harness_status_detail})"
    return Result(
        name=case.name, passed=passed, status=status, detail=detail,
        seconds=round(elapsed, 3), turns=state.harness_spent_turns,
        prompt_tokens=meter.prompt_tokens, completion_tokens=meter.completion_tokens,
        provider_reported_turns=meter.provider_reported_turns,
        metered_turns=meter.metered_turns, cost_usd=None,
        tools=tuple(row.get("tool", "") for row in state.harness_activity),
    )


def _price(result: Result, rates: tuple[float, float] | None) -> float | None:
    if rates is None:
        return None
    return round((result.prompt_tokens * rates[0] + result.completion_tokens * rates[1]) / 1_000_000, 6)


async def run_suite(provider: str, model: str, *, base_url: str | None = None,
                    timeout: float = 120, max_turns: int = 12,
                    prices: tuple[float, float] | None = None, api_key: str = "") -> dict:
    from backend.token_counter import price_per_mtok
    from backend.harness import workspace as workspace_module

    if "CI" in os.environ or "GITHUB_ACTIONS" in os.environ:
        raise RuntimeError("Live workspace benchmarks are disabled in CI")
    runtime, model_ref = _runtime(provider, model, base_url, api_key)
    rates = prices if prices is not None else price_per_mtok(provider, model_ref.model_id)
    results: list[Result] = []
    with tempfile.TemporaryDirectory(prefix="graphlink-workspace-benchmark-") as temporary:
        # Repoint only this process's scratch root. The production harness
        # still resolves, grants and confines paths through its usual code.
        with mock.patch.object(workspace_module, "HARNESS_WORKSPACE_ROOT", Path(temporary) / "workspaces"):
            for case in CASES:
                try:
                    result = await _drive(case, runtime, model_ref, timeout, max_turns)
                except Exception as exc:
                    result = Result(case.name, False, "error", f"benchmark error: {exc}",
                                    0.0, 0, 0, 0, 0, 0, None, ())
                results.append(Result(**{**asdict(result), "cost_usd": _price(result, rates)}))
    passed = sum(r.passed for r in results)
    costs = [r.cost_usd for r in results]
    return {
        "provider": provider, "model": model_ref.model_id,
        "cases": [asdict(r) for r in results],
        "summary": {
            "passed": passed, "total": len(results),
            "pass_rate": round(passed / len(results), 4),
            "seconds": round(sum(r.seconds for r in results), 3),
            "prompt_tokens": sum(r.prompt_tokens for r in results),
            "completion_tokens": sum(r.completion_tokens for r in results),
            "provider_reported_turns": sum(r.provider_reported_turns for r in results),
            "metered_turns": sum(r.metered_turns for r in results),
            "cost_usd": round(sum(costs), 6) if all(c is not None for c in costs) else None,
            "pricing_usd_per_million_tokens": (
                {"input": rates[0], "output": rates[1]} if rates is not None else None
            ),
        },
    }


def _write_line(message: str) -> None:
    """Write one CLI output line to stdout."""
    sys.stdout.write(f"{message}\n")


def _print_report(report: dict) -> None:
    _write_line(f"Workspace-agent benchmark: {report['provider']} / {report['model']}")
    _write_line("Case                         Pass  Seconds  Input tok  Output tok  Cost USD")
    for row in report["cases"]:
        cost = "n/a" if row["cost_usd"] is None else f"USD {row['cost_usd']:.6f}"
        _write_line(f"{row['name']:<28} {'yes' if row['passed'] else 'no':<5} "
              f"{row['seconds']:>7.2f} {row['prompt_tokens']:>10} "
              f"{row['completion_tokens']:>11} {cost:>9}")
        if not row["passed"]:
            _write_line(f"  FAIL: {row['detail']}")
    total = report["summary"]
    cost = "n/a (unknown price)" if total["cost_usd"] is None else f"USD {total['cost_usd']:.6f}"
    _write_line(f"Pass rate: {total['passed']}/{total['total']} ({total['pass_rate']:.1%})")
    _write_line(f"Elapsed: {total['seconds']:.2f}s; tokens: {total['prompt_tokens']} input + "
          f"{total['completion_tokens']} output; estimated cost: {cost}")
    _write_line(f"Provider-reported usage: {total['provider_reported_turns']}/{total['metered_turns']} "
          "turns (remaining token counts are estimates)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly allow live provider calls and temporary file writes")
    parser.add_argument("--provider", required=True, help="Graphlink provider name, e.g. OpenAI, Anthropic Claude, Ollama")
    parser.add_argument("--model", required=True, help="Exact model ID (for Llama.cpp, the path to a local .gguf file)")
    parser.add_argument("--base-url", help="Optional cloud API base URL")
    parser.add_argument("--api-key-env", help="Environment variable holding the selected provider API key (never pass the key itself)")
    parser.add_argument("--timeout", type=float, default=120, help="Timeout per task in seconds (default: 120)")
    parser.add_argument("--max-turns", type=int, default=12, help="Model turns allowed per task (default: 12)")
    parser.add_argument("--input-price", type=float, help="USD per million input tokens (override)")
    parser.add_argument("--output-price", type=float, help="USD per million output tokens (override)")
    parser.add_argument("--json-out", type=Path, help="Optional local JSON report path")
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("Live benchmarks are opt-in. Pass --run to make provider calls.")
    if "CI" in os.environ or "GITHUB_ACTIONS" in os.environ:
        parser.error("Live workspace benchmarks are disabled in CI.")
    if not args.provider.strip() or not args.model.strip():
        parser.error("Provider and model must be nonempty.")
    if not 0 < args.timeout <= 3600 or not 1 <= args.max_turns <= 50:
        parser.error("Use --timeout between 0 and 3600 and --max-turns between 1 and 50.")
    if (args.input_price is None) != (args.output_price is None):
        parser.error("Provide both --input-price and --output-price, or neither.")
    if args.input_price is not None and (not 0 <= args.input_price < float("inf") or
                                         not 0 <= args.output_price < float("inf")):
        parser.error("Token prices must be finite and nonnegative.")
    prices = ((args.input_price, args.output_price) if args.input_price is not None else None)
    if args.api_key_env and args.api_key_env not in os.environ:
        parser.error(f"API key environment variable {args.api_key_env!r} is not set")
    api_key = os.environ.get(args.api_key_env, "") if args.api_key_env else ""
    try:
        report = asyncio.run(run_suite(args.provider, args.model, base_url=args.base_url,
                                       timeout=args.timeout, max_turns=args.max_turns,
                                       prices=prices, api_key=api_key))
    except Exception as exc:
        parser.exit(2, f"Benchmark setup failed: {exc}\n")
    _print_report(report)
    if args.json_out is not None:
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        _write_line(f"JSON report: {args.json_out}")
    return 0 if report["summary"]["passed"] == report["summary"]["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
