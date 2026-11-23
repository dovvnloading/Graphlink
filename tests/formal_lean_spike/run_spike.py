"""Measure the checked-in fixtures. No user-supplied source or checker commands."""

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from runner import StageTimeout, run_stage


HERE = Path(__file__).resolve().parent
FIXTURES = {
    "positive": ("accepted", []),
    "changed_target": ("statement_mismatch", []),
    "sorry": ("forbidden_axiom", ["sorryAx"]),
    "new_axiom": ("forbidden_axiom", ["Graphlink.Spike.extraAssumption"]),
    "target_propext": ("accepted", ["propext"]),
    "imported_axiom": ("forbidden_axiom", ["Graphlink.Dependency.assumed"]),
    "imported_sorry": ("forbidden_axiom", ["sorryAx"]),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sha256_hex(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def validate_toolchain(probe, lock_bytes):
    lock = json.loads(lock_bytes)
    evidence = probe["toolchain"]
    expected = {
        "lock_sha256": digest(lock_bytes),
        "lean_revision": lock["lean"]["revision"],
        "mathlib_revision": lock["mathlib"]["revision"],
    }
    if any(evidence.get(key) != value for key, value in expected.items()):
        raise ValueError("The image toolchain differs from the source lock")
    if evidence.get("executables") != lock["lean"]["executables"]:
        raise ValueError("Image checker binary identities differ from the source lock")
    if not sha256_hex(evidence.get("modules_sha256")):
        raise ValueError("Missing compiled module inventory digest")


def require_stage(image_id, stage, payload):
    result = run_stage(image_id, stage, payload)
    if result.exit_code != 0:
        diagnostic = result.stderr[-4096:].decode(errors="replace")
        raise ValueError(f"{stage} exited {result.exit_code}: {diagnostic}")
    return result


def validate_replay(name, artifact, measurement):
    outcome, axioms = FIXTURES[name]
    accepted = outcome == "accepted"
    expected = {
        "schemaVersion": 1,
        "solutionExportSha256": digest(artifact),
        "target": "Graphlink.Spike.addZero",
        "policy": "no-axioms-v1",
        "replayEnvironmentAxioms": ["propext"],
        "statementMatches": outcome != "statement_mismatch",
        "axioms": axioms,
        "comparatorExit": 0 if accepted else 1,
        "comparatorOutcome": outcome,
        "targetPolicyOutcome": "forbidden_axiom" if axioms else outcome,
        "replayedBy": ["nanoda", "Lean default"] if accepted else [],
        "strictAssurance": "disabled",
    }
    if not isinstance(measurement, dict):
        raise ValueError(f"{name}: malformed replay measurement")
    for key, value in expected.items():
        actual = measurement.get(key)
        if type(actual) is not type(value) or actual != value:
            raise ValueError(f"{name}: expected {key}={value!r}, received {actual!r}")
    challenge_hash = measurement.get("challengeExportSha256")
    if not sha256_hex(challenge_hash):
        raise ValueError(f"{name}: invalid challenge digest")
    challenge_type = measurement.get("challengeType")
    if not isinstance(challenge_type, str) or not challenge_type.strip():
        raise ValueError(f"{name}: missing elaborated type")
    return challenge_hash, challenge_type


def exercise(image_id, report):
    probe = require_stage(image_id, "probe", b"")
    report["isolation"] = json.loads(probe.stdout)
    validate_toolchain(report["isolation"], (HERE / "toolchain.lock.json").read_bytes())
    # The runner raises StageTimeout only after cleanup succeeds.
    try:
        run_stage(image_id, "wait", b"", timeout=5)
    except StageTimeout as exc:
        if exc.stdout != b"wait-started\n":
            raise ValueError("The wait fixture timed out before entering its wait") from exc
        report["timedOutFixtureRemoved"] = True
    else:
        raise ValueError("The fixed wait fixture escaped its deadline")

    first_identity = None
    first_positive = None
    for name in [*FIXTURES, "positive"]:
        compiled = require_stage(image_id, "export", name.encode())
        replay = require_stage(image_id, "replay", compiled.stdout)
        measurement = json.loads(replay.stdout)
        identity = validate_replay(name, compiled.stdout, measurement)
        if first_identity is None:
            first_identity = identity
            first_positive = measurement["solutionExportSha256"]
        if identity != first_identity:
            raise ValueError(f"{name}: trusted challenge changed between fixtures")
        if name == "positive" and measurement["solutionExportSha256"] != first_positive:
            raise ValueError("The clean rerun changed the positive fixture export")
        report["fixtures"].append({
            "name": name,
            "replay": measurement,
            "exportDiagnostic": compiled.stderr[-4096:].decode(errors="replace"),
            "replayDiagnostic": replay.stderr[-4096:].decode(errors="replace"),
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-id", required=True, help="Local immutable sha256 image ID")
    parser.add_argument("--report", required=True, type=Path, help="JSON transcript destination")
    args = parser.parse_args()
    report = {
        "schemaVersion": 1,
        "imageId": args.image_id,
        "toolchainLockSha256": digest((HERE / "toolchain.lock.json").read_bytes()),
        "startedAt": datetime.now(UTC).isoformat(),
        "strictAssurance": "disabled",
        "fixtures": [],
        "fixtureRunComplete": False,
    }
    try:
        exercise(args.image_id, report)
        report["fixtureRunComplete"] = True
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        report["error"] = str(exc)
        sys.stderr.write(json.dumps({"error": str(exc)}) + "\n")
    finally:
        report["finishedAt"] = datetime.now(UTC).isoformat()
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["fixtureRunComplete"] else 1


if __name__ == "__main__":
    sys.exit(main())
