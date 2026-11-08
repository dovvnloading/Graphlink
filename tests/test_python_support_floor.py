"""Packaging must not advertise unsupported Python versions.

The README and 3.12-resolved hash lock are the distribution's supported
installation path. Python 3.10/3.11 cannot install that lockfile.
"""

import tomllib
from pathlib import Path


def test_package_metadata_matches_supported_python_floor():
    repo_root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["requires-python"] == ">=3.12"
