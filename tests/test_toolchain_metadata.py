"""Keep the advertised Python floor and cross-platform tooling aligned."""

from __future__ import annotations

import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _config() -> dict:
    with PROJECT.open("rb") as stream:
        return tomllib.load(stream)


def test_ruff_target_matches_supported_python_floor():
    config = _config()
    assert config["project"]["requires-python"] == ">=3.12"
    assert config["tool"]["ruff"]["target-version"] == "py312"


def test_mutmut_is_not_forced_onto_native_windows_dev_installs():
    extras = _config()["project"]["optional-dependencies"]
    assert not any(req.lower().startswith("mutmut") for req in extras["dev"])
    assert extras["mutation"] == ["mutmut; sys_platform != 'win32'"]
