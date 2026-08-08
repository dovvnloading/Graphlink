"""ADR-016 stage 16.5 eval harness CLI.

    python -m backend.evals            # default: scripted responses, deterministic, no network
    python -m backend.evals --live     # real, already-configured provider - see README.md

Prints a pass/fail/not_implemented table and exits nonzero if ANY result has
status == "failed". A "not_implemented" result (the agent_build dimension,
today) is expected/informational and never affects the exit code.
"""

from __future__ import annotations

import argparse
import sys

from backend.evals.runner import EvalResult, run_all

_STATUS_LABEL = {
    "passed": "PASS",
    "failed": "FAIL",
    "not_implemented": "N/A ",
}


def _print_table(results: list[EvalResult]) -> None:
    if not results:
        print("No eval fixtures found.")
        return

    name_width = max(len("fixture"), *(len(r.fixture_name) for r in results))
    kind_width = max(len("kind"), *(len(r.kind) for r in results))

    header = f"{'STATUS':<5} {'kind':<{kind_width}} {'fixture':<{name_width}}  detail"
    print(header)
    print("-" * len(header))
    for result in results:
        label = _STATUS_LABEL.get(result.status, result.status.upper())
        first_line = result.detail.splitlines()[0] if result.detail else ""
        print(f"{label:<5} {result.kind:<{kind_width}} {result.fixture_name:<{name_width}}  {first_line}")
        for extra_line in result.detail.splitlines()[1:]:
            print(" " * (5 + 1 + kind_width + 1 + name_width + 2) + extra_line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.evals",
        description="ADR-016 stage 16.5 local eval harness (chart / structured_output / agent_build).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="skip the scripted-response patch and call the real, already-configured provider",
    )
    args = parser.parse_args(argv)

    results = run_all(live=args.live)
    _print_table(results)

    passed = sum(1 for r in results if r.status == "passed")
    failed = sum(1 for r in results if r.status == "failed")
    not_implemented = sum(1 for r in results if r.status == "not_implemented")
    print()
    print(f"{passed} passed, {failed} failed, {not_implemented} not_implemented ({len(results)} total)")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
