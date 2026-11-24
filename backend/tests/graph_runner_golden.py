"""Stable wire projections from the pre-registry Graph Runner test cases.

Keep literal hashes for deterministic text and CSV. Live IDs, request UUIDs
and times are not byte-stable across sessions; verify identity relationships
before substituting placeholders. Expected results never come from adapters.
"""
import json
import re
from pathlib import Path

_GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "graph_runner_legacy.json").read_text(encoding="utf-8"))


def assert_golden(name, actual):
    def encode(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")

    assert encode(actual) == encode(_GOLDEN[name])


def step_fields(steps, *fields):
    return [{key: step[key] for key in fields} for step in steps]


def remap_step(step, ids):
    return {**step, "nodeId": ids[step["nodeId"]], "sourceId": ids[step["sourceId"]]}


def fence(step):
    attempt = step["externalAttempt"].copy()
    assert re.fullmatch(r"graph-[a-f0-9]{32}", attempt["requestId"])
    assert re.fullmatch(r"[a-f0-9]{64}", attempt["inputHash"])
    assert attempt["inputHash"] == step["inputHash"]
    assert attempt["inputRef"] == "mcp-input:" + step["inputHash"]
    attempt.update(requestId="<requestId>", inputHash="<inputHash>", inputRef="mcp-input:<inputHash>")
    return {"status": step["status"], "detail": step["detail"], "attempt": attempt}
