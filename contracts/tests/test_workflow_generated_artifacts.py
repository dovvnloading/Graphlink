"""Non-topic workflow artifacts must be current without introducing an API."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from setuptools import find_namespace_packages

from contracts.workflow_codegen import OUTPUT, ROOTS, generated_workflow_artifacts, schema_for

ROOT = Path(__file__).resolve().parents[2]


def test_workflow_artifacts_match_generator():
    artifacts = generated_workflow_artifacts()
    assert set(artifacts) == {OUTPUT / f"{name}.schema.json" for name in ROOTS} | {OUTPUT / "workflow.ts"}
    for path, content in artifacts.items():
        assert path.read_text(encoding="utf-8") == content, f"Regenerate {path} with python contracts/codegen.py --write"
    topics = (ROOT / "web_ui/src/lib/api-contract/topics.ts").read_text(encoding="utf-8")
    assert "workflow-contract" not in topics


@pytest.mark.parametrize("name", ROOTS)
def test_schema_is_standalone_with_resolvable_refs(name):
    schema = json.loads(schema_for(ROOTS[name]))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"

    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"]
                assert ref.startswith("#/$defs/") and ref.removeprefix("#/$defs/") in schema["$defs"]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)


def test_discriminated_ports_recursive_json_and_frozen_spec_schema():
    defs = json.loads(schema_for(ROOTS["port-value"]))["$defs"]
    assert [defs[name]["properties"]["kind"]["enum"][0] for name in (
        "TextValue", "JsonValue", "TableValue", "FileValue", "ImageValue",
    )] == ["text", "json", "table", "file", "image"]
    assert {"type": "array", "items": {"$ref": "#/$defs/JsonData"}} in defs["JsonData"]["anyOf"]
    assert defs["FileValue"]["additionalProperties"] is False
    spec = json.loads(schema_for(ROOTS["workflow-spec"]))["$defs"]["WorkflowSpec"]
    assert spec["properties"]["schema_version"] == {"enum": [1]}
    assert spec["properties"]["steps"]["type"] == "array"


def test_workflow_package_is_shipped_and_measured():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]
    discovery = config["setuptools"]["packages"]["find"]
    packages = find_namespace_packages(where=str(ROOT), include=discovery["include"], exclude=discovery["exclude"])
    assert "backend.workflow" in packages
    package_path = ROOT / "backend/workflow"
    assert any(package_path.is_relative_to(ROOT / source) for source in config["coverage"]["run"]["source"])
