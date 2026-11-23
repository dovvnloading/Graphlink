"""Compare reproducible evidence from two independent CI workers."""

import json
import sys
from pathlib import Path


def identity(report):
    if report["fixtureRunComplete"] is not True or report["timedOutFixtureRemoved"] is not True:
        raise ValueError("The fixture run must finish before comparison")
    return {
        "imageId": report["imageId"],
        "toolchainLockSha256": report["toolchainLockSha256"],
        "toolchain": report["isolation"]["toolchain"],
        "fixtures": [(row["name"], row["replay"]) for row in report["fixtures"]],
    }


def compare(first, second):
    left, right = identity(first), identity(second)
    for key in left:
        if left[key] != right[key]:
            raise ValueError(f"Clean builds disagree on {key}")


if __name__ == "__main__":
    first, second = (json.loads(Path(path).read_bytes()) for path in sys.argv[1:])
    compare(first, second)
    sys.stdout.write(f"Both clean builds reproduced {first['imageId']} and all fixture measurements.\n")
