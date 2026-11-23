import json
import unittest

from compare_runs import compare
import run_spike


class TestEvidence(unittest.TestCase):
    def test_clean_build_comparison_ignores_clock_and_requires_image_and_proof_identity(self):
        path = run_spike.HERE.parents[1] / "docs/evidence/formal_lean_p0_run_45179416012.json"
        first = json.loads(path.read_bytes())
        compare(first, {**first, "startedAt": "another time"})
        with self.assertRaisesRegex(ValueError, "imageId"):
            compare(first, {**first, "imageId": "sha256:" + "0" * 64})
        with self.assertRaisesRegex(ValueError, "fixtures"):
            compare(first, {**first, "fixtures": first["fixtures"][:-1]})

    def test_probe_must_match_binary_and_import_lock(self):
        raw = (run_spike.HERE / "toolchain.lock.json").read_bytes()
        lock = json.loads(raw)
        evidence = {
            "lock_sha256": run_spike.digest(raw),
            "lean_revision": lock["lean"]["revision"],
            "mathlib_revision": lock["mathlib"]["revision"],
            "executables": lock["lean"]["executables"].copy(),
            "modules_sha256": "1" * 64,
        }
        run_spike.validate_toolchain({"toolchain": evidence}, raw)
        evidence["executables"]["nanoda_bin"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "binary identities"):
            run_spike.validate_toolchain({"toolchain": evidence}, raw)

    def test_wrong_target_rejection_binds_candidate_and_reason(self):
        data = b"fixture"
        report = {
            "schemaVersion": 1,
            "solutionExportSha256": run_spike.digest(data),
            "challengeExportSha256": "2" * 64,
            "challengeType": "elaborated test type",
            "target": "Graphlink.Spike.addZero",
            "policy": "no-axioms-v1",
            "replayEnvironmentAxioms": ["propext"],
            "statementMatches": False,
            "axioms": [],
            "comparatorExit": 1,
            "comparatorOutcome": "statement_mismatch",
            "targetPolicyOutcome": "statement_mismatch",
            "replayedBy": [],
            "strictAssurance": "disabled",
        }
        run_spike.validate_replay("changed_target", data, report)
        for key, value in (
            ("solutionExportSha256", "1" * 64),
            ("comparatorOutcome", "unavailable"),
            ("statementMatches", 0),
            ("strictAssurance", "verified"),
            ("challengeType", ""),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                run_spike.validate_replay("changed_target", data, {**report, key: value})
