"""Image-internal, fixed-fixture checker spike. Binary exports are the only interchange."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ASSETS = Path("/opt/graphlink/checker")
CHALLENGE = ASSETS / "Challenge.ndjson"
TARGET = "Graphlink.Spike.addZero"
FIXTURES = {"positive", "changed_target", "sorry", "new_axiom", "imported_axiom", "imported_sorry", "target_propext"}
EXPORT_LIMIT = 16 * 1024 * 1024
LOG_LIMIT = 64 * 1024

# The pinned kernel's primitive closure contains propext. The target policy
# separately requires an empty list measured from the named theorem.
REPLAY_ENVIRONMENT_AXIOMS = ("propext",)

# lake comparator's primitiveTargets at the locked Lean revision. Supplying an
# export bypasses its exporter, so we must request its full set of primitive roots.
PRIMITIVES = (
    "Nat.add", "Nat.sub", "Nat.mul", "Nat.pow", "Nat.gcd", "Nat.div",
    "Nat.mod", "Nat.beq", "Nat.ble", "Nat.land", "Nat.lor", "Nat.xor",
    "Nat.shiftLeft", "Nat.shiftRight", "String.ofList", "Char.ofNat", "List",
    "eagerReduce", "Nat", "String", "String.mk", "Char", "optParam", "autoParam",
    "semiOutParam", "outParam",
)


def read_bounded(stream, limit: int) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Stage input or child output exceeds its byte budget")
    return data


def run_command(args: list[str], cwd: Path, env: dict[str, str], output=None) -> tuple[int, bytes]:
    # The outer launcher enforces the deadline and kills the complete container.
    # Disk-backed streams avoid an unbounded capture in memory. RLIMIT_FSIZE
    # (set before invoking any child) and the container tmpfs cap bound disk use.
    with tempfile.TemporaryFile() as log:
        code = subprocess.run(
            args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=log if output is None else output, stderr=log, check=False,
        ).returncode
        log.seek(0)
        diagnostic = read_bounded(log, LOG_LIMIT)
    argv = [str(arg) for arg in args]
    # stderr is reserved for the bounded transcript; stdout carries stage data.
    sys.stderr.write(json.dumps({"argv": argv, "exit": code}) + "\n")
    return code, diagnostic


def require_success(args: list[str], cwd: Path, env: dict[str, str], output=None) -> bytes:
    code, log = run_command(args, cwd, env, output)
    if code:
        sys.stderr.buffer.write(log)
        raise ValueError(f"{args[0]} stage failed with exit {code}")
    return log


def local_env(work: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["LEAN_PATH"] = os.pathsep.join([str(work), env["LEAN_PATH"]])
    env["LEAN_ABORT_ON_PANIC"] = "1"
    return env


def compile_module(source: Path, module: str, work: Path, env: dict[str, str]) -> None:
    filename = f"{module}.lean"
    shutil.copyfile(source, work / filename)
    require_success(["lean", "-o", f"{module}.olean", filename], work, env)


def export_module(source: Path, module: str, work: Path, output) -> None:
    env = local_env(work)
    if source.stem in {"imported_axiom", "imported_sorry"}:
        compile_module(ASSETS / "fixtures" / "Dependency.lean", "Dependency", work, env)
    compile_module(source, module, work, env)
    require_success(["leanexport", module, "--", TARGET, *REPLAY_ENVIRONMENT_AXIOMS, *PRIMITIVES], work, env, output)
    size = output.tell()
    if not 0 < size < EXPORT_LIMIT:
        raise ValueError("Incomplete or oversized Lean export")


def inspect_export(solution: Path) -> dict:
    raw = require_success(
        ["lean", "--run", "Inspect.lean", str(CHALLENGE), str(solution)],
        ASSETS, os.environ.copy(),
    )
    inspected = json.loads(raw)
    if set(inspected) != {"target", "challengeType", "statementMatches", "axioms"}:
        raise ValueError("Unexpected audit report shape")
    if inspected["target"] != TARGET or not isinstance(inspected["challengeType"], str) or not inspected["challengeType"]:
        raise ValueError("Incomplete challenge identity")
    axioms = inspected["axioms"]
    if type(inspected["statementMatches"]) is not bool or not isinstance(axioms, list):
        raise ValueError("Incomplete audit evidence")
    if any(not isinstance(name, str) or not name for name in axioms) or axioms != sorted(set(axioms)):
        raise ValueError("Invalid axiom report")
    return inspected


def classify_report(facts: dict, code: int, log: bytes) -> tuple[str, list[str], str]:
    # Exclusively the fresh replay container produces this log. The candidate
    # compiler's streams never reach this function. A tool failure, even with
    # comparator's generic exit 1, cannot satisfy an expected semantic rejection.
    lines = log.decode("utf-8", errors="strict").splitlines()
    if not facts["statementMatches"]:
        if code == 1 and f"error: Challenge and solution theorem statement do not match: '{TARGET}'" in lines:
            return "statement_mismatch", [], "statement_mismatch"
    elif rejected := set(facts["axioms"]).difference(REPLAY_ENVIRONMENT_AXIOMS):
        if code == 1 and any(f"error: Illegal axiom detected: '{name}'" in lines for name in rejected):
            return "forbidden_axiom", [], "forbidden_axiom"
    else:
        kernels = ["nanoda", "Lean default"]
        if code == 0 and "Your solution is okay!" in lines and all(
            f"{name} kernel accepts the solution" in lines for name in kernels
        ):
            target_outcome = "forbidden_axiom" if facts["axioms"] else "accepted"
            return "accepted", kernels, target_outcome
    sys.stderr.buffer.write(log)
    raise ValueError("Comparator result and independent proof measurements disagree or a checker is unavailable")


def replay(data: bytes, work: Path) -> dict:
    solution = work / "Solution.ndjson"
    solution.write_bytes(data)
    facts = inspect_export(solution)
    # A fresh outer container is the isolation boundary. Both sides are exports:
    # this invocation loads no candidate module, builds no project and resolves
    # no dependencies. Nested bwrap would require additional namespace rights.
    args = [
        "lake", "comparator", "--config", str(ASSETS / "comparator.json"),
        "--challenge-from-export", str(CHALLENGE), "--solution-from-export", str(solution),
        "--inadvisably-no-sandbox",
    ]
    code, log = run_command(args, work, os.environ.copy())
    outcome, kernels, target_outcome = classify_report(facts, code, log)
    sys.stderr.write(log.decode("utf-8", errors="strict"))
    return {
        **facts,
        "schemaVersion": 1,
        "challengeExportSha256": hashlib.sha256(CHALLENGE.read_bytes()).hexdigest(),
        "solutionExportSha256": hashlib.sha256(data).hexdigest(),
        "policy": "no-axioms-v1",
        "replayEnvironmentAxioms": list(REPLAY_ENVIRONMENT_AXIOMS),
        "targetPolicyOutcome": target_outcome,
        "comparatorExit": code,
        "comparatorOutcome": outcome,
        "replayedBy": kernels,
        "strictAssurance": "disabled",
    }


def run(stage: str) -> None:
    if Path(__file__).resolve().parent != ASSETS:
        raise ValueError("Run this image-internal stage using the isolated spike launcher")
    import resource  # Linux image only

    current, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    limits = [EXPORT_LIMIT, *(v for v in (current, hard) if v != resource.RLIM_INFINITY)]
    resource.setrlimit(resource.RLIMIT_FSIZE, (min(limits), hard))
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for tool in ("lean", "lake"):
            version = require_success([tool, "--version"], work, os.environ.copy())
            sys.stderr.write(json.dumps({"tool": tool, "version": version.decode().strip()}) + "\n")
        if stage == "prepare":
            with CHALLENGE.open("wb") as output:
                export_module(ASSETS / "Challenge.lean", "Challenge", work, output)
            self_test = inspect_export(CHALLENGE)
            if self_test["axioms"] != ["sorryAx"] or not self_test["statementMatches"]:
                raise ValueError("Challenge placeholder self-test failed")
        elif stage == "export":
            fixture = read_bounded(sys.stdin.buffer, 64).decode("ascii")
            if fixture not in FIXTURES:
                raise ValueError("Unknown fixture")
            with tempfile.TemporaryFile() as output:
                export_module(ASSETS / "fixtures" / f"{fixture}.lean", "Solution", work, output)
                output.seek(0)
                shutil.copyfileobj(output, sys.stdout.buffer)
        elif stage == "replay":
            result = replay(read_bounded(sys.stdin.buffer, EXPORT_LIMIT), work)
            sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
        else:
            raise ValueError("Unknown stage")


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("Expected one stage")
        run(sys.argv[1])
    except (OSError, ValueError, KeyError) as exc:
        sys.stderr.write(f"Spike stage unavailable: {exc}\n")
        sys.exit(2)
