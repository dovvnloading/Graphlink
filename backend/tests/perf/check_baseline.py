"""ADR-019 stage 19.3: nightly wall-clock regression check against the
committed baseline.json - the "local/self-hosted... ±20% regression
threshold... a 2x regression fails loudly" half of the ADR's §4 split (the
OTHER half, stage 19.2's counting assertions, already runs per-PR in the
main ci.yml). Deliberately its own script, not folded into
measure_baselines.py: that one's job is "print today's numbers"
(used interactively, by a human, to update baseline.json on purpose),
this one's job is "fail loudly if today's numbers drifted from what's
committed" (used non-interactively, by the nightly workflow).

Two metric classes, two tolerance profiles:

- TIMING metrics (payload_build_ms, serialize_ms, publish_total_ms) are
  genuinely noisy wall-clock measurements - CPU scheduling jitter, not
  just real code changes, moves these run to run. ADR-019 section 4's own
  thresholds apply: >20% is a SOFT regression (reported, fails the job),
  >=100% (2x) is called out as SEVERE in the failure message specifically
  (the ADR's own "fails loudly" language). Below
  MIN_TIMING_MS_FOR_PCT_CHECK, the baseline value itself is too close to
  the timer's own resolution for a percentage to mean anything (0.15ms ->
  0.19ms is already +26% and is very plausibly pure noise) - skipped from
  the pass/fail check entirely, just reported.
- SIZE metrics (full_snapshot_kib, single_node_payload_kib) are
  deterministic byte counts from a fixed fixture - there is no timer
  jitter to account for, so any real drift beyond float-rounding IS a
  real code change (a wire field added/removed, a serialization tweak).
  Tighter thresholds than timing: >2% is worth a report, >10% fails.

fixture_build_ms is measured and printed for visibility but deliberately
NOT gated (REPORTED_ONLY_METRICS, not TIMING_METRICS): it times
`workload.build()` - constructing the synthetic test fixture itself
(measure_baselines.py's own `_measure`), which is test-harness overhead,
not a real app code path, and isn't one of ADR-019 §2's budgeted rows
(those are payload_build_ms/serialize_ms/publish_total_ms, all real
publish-path work). Gating on it was a real incident, not a hypothetical:
on 2026-08-24, `large`/`stress`'s fixture_build_ms swung +289%/+113% in a
single run (8.75ms->34.0ms, 61.85ms->131.48ms) - both well above
MIN_TIMING_MS_FOR_PCT_CHECK so not caught by that filter - while every
genuinely budgeted metric in the SAME run stayed inside the 20% soft
threshold. A git-log review of nightly-perf.yml's run history found this
was the sole cause in 5 of the last 13 nightly runs: pure shared-runner
scheduling noise on fixture construction (large object-allocation churn,
no I/O, nothing the app's own code touches), gating nobody was
triaging - exactly the "flaky perf gates get disabled and then nothing is
enforced" failure mode ADR-019's own "Alternatives considered" section
already named as the reason CI counting-assertions (not wall-clock) are
the per-PR gate, applied here to the nightly wall-clock tier too.

A failure here does not necessarily mean something got SLOWER - it just
as often means baseline.json itself is stale and needs a deliberate,
reviewed regeneration (see that file's own header comment). Distinguishing
"real regression" from "stale baseline" is a human judgment call this
script cannot make; it only surfaces the number.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from backend.tests.perf.graph_factory import ALL_WORKLOADS
from backend.tests.perf.measure_baselines import _measure

BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"

TIMING_METRICS = ("payload_build_ms", "serialize_ms", "publish_total_ms")
# Measured and printed, never gated - see this module's own docstring for
# the 2026-08-24 incident that made this a deliberate exclusion, not an
# oversight.
REPORTED_ONLY_METRICS = ("fixture_build_ms",)
SIZE_METRICS = ("full_snapshot_kib", "single_node_payload_kib")

MIN_TIMING_MS_FOR_PCT_CHECK = 1.0
TIMING_SOFT_THRESHOLD = 0.20  # ADR-019 §4: "±20% regression threshold"
TIMING_SEVERE_THRESHOLD = 1.00  # ADR-019 §4: "a 2x regression fails loudly"
SIZE_SOFT_THRESHOLD = 0.02
SIZE_FAIL_THRESHOLD = 0.10


def _pct_change(baseline: float, current: float) -> float:
    if baseline == 0:
        return 0.0 if current == 0 else float("inf")
    return (current - baseline) / baseline


def check_workload(baseline: dict, current: dict) -> list[str]:
    """Returns a list of human-readable failure lines for one workload -
    empty means clean. Never raises; every metric is checked independently
    so one bad number doesn't hide the others."""
    failures: list[str] = []
    name = baseline["name"]

    for metric in TIMING_METRICS:
        base_val, cur_val = baseline[metric], current[metric]
        if base_val < MIN_TIMING_MS_FOR_PCT_CHECK:
            continue
        change = _pct_change(base_val, cur_val)
        if change >= TIMING_SEVERE_THRESHOLD:
            failures.append(
                f"{name}.{metric}: SEVERE regression - {base_val}ms -> {cur_val}ms "
                f"({change:+.0%}, >= 2x the baseline)"
            )
        elif change >= TIMING_SOFT_THRESHOLD:
            failures.append(
                f"{name}.{metric}: {base_val}ms -> {cur_val}ms ({change:+.0%}, over the {TIMING_SOFT_THRESHOLD:.0%} threshold)"
            )

    for metric in SIZE_METRICS:
        base_val, cur_val = baseline[metric], current[metric]
        change = _pct_change(base_val, cur_val)
        if abs(change) >= SIZE_FAIL_THRESHOLD:
            failures.append(
                f"{name}.{metric}: {base_val} KiB -> {cur_val} KiB "
                f"({change:+.0%}, over the {SIZE_FAIL_THRESHOLD:.0%} threshold - deterministic metric, "
                "this is a real change, not noise)"
            )

    return failures


def main() -> int:
    baseline_data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline_by_name = {w["name"]: w for w in baseline_data["workloads"]}

    current_by_name = {w.name: _measure(w) for w in ALL_WORKLOADS}

    if set(baseline_by_name) != set(current_by_name):
        print(
            f"baseline.json's workload set {sorted(baseline_by_name)} no longer matches "
            f"graph_factory.ALL_WORKLOADS {sorted(current_by_name)} - regenerate baseline.json.",
            file=sys.stderr,
        )
        return 1

    all_failures: list[str] = []
    print(f"{'workload':<10} {'metric':<22} {'baseline':>12} {'current':>12} {'change':>8}")
    for name, baseline in baseline_by_name.items():
        current = current_by_name[name]
        for metric in (*REPORTED_ONLY_METRICS, *TIMING_METRICS, *SIZE_METRICS):
            base_val, cur_val = baseline[metric], current[metric]
            change = _pct_change(base_val, cur_val)
            print(f"{name:<10} {metric:<22} {base_val:>12} {cur_val:>12} {change:>+7.0%}")
        all_failures.extend(check_workload(baseline, current))

    print()
    if all_failures:
        print(f"{len(all_failures)} regression(s) against baseline.json (measured {baseline_data['measured_at']}):")
        for line in all_failures:
            print(f"  - {line}")
        print(
            "\nIf this is a real regression, fix it. If baseline.json is just stale "
            "(a deliberate, reviewed performance-affecting change landed), regenerate it: "
            "python -m backend.tests.perf.measure_baselines, then update baseline.json's "
            "numbers and measured_at by hand and commit that as its own reviewed change."
        )
        return 1

    print("All workloads within threshold of baseline.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
