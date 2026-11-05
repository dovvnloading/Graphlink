"""Pure regression and adversarial tests for Artifact section patches."""

from __future__ import annotations

import json

import pytest

import api_provider
import backend.structured_output as structured
from graphlink_artifact_agent import ArtifactAgent
from graphlink_plugins.artifact.section_edits import (
    MAX_OPERATIONS,
    MAX_REPLACEMENT_CHARS,
    SECTION_PATCH_SCHEMA,
    SECTION_PATCH_SYSTEM_PROMPT,
    SectionEditError,
    apply_section_patch,
    patch_messages,
    select_edit_plan,
    sha256_text,
    split_sections,
    validate_structured_result_size,
)


DOC = (
    "# Project Plan\n\nOpening paragraph.\n\n"
    "## Scope\n\nKeep this scope text.\n\n"
    "## Risks\n\nOld risk text.\n\n"
    "## Timeline\n\nDo not change this schedule.\n"
)


def _patch(document: str, name: str, replacement: str) -> dict:
    target = next(s for s in split_sections(document) if s.heading == name)
    return {
        "version": 1, "base_sha256": sha256_text(document),
        "operations": [{
            "kind": "replace-section", "section_id": target.section_id,
            "expected_sha256": target.sha256, "replacement_markdown": replacement,
        }],
        "summary": "Updated the requested section.",
    }


def test_headings_inside_code_fences_or_indented_code_are_ignored():
    source = (
        "# Overview\n~~~markdown\n## Fake tilde heading\n~~~\n"
        "    ## Indented text is not a heading\n\n## Actual\nText.\n"
        + chr(96) * 3 + "python\n### Fake backtick heading\n"
        + chr(96) * 3 + "\n"
    )
    sections = split_sections(source)
    assert [s.heading for s in sections] == ["Overview", "Actual"]
    assert "".join(s.text for s in sections) == source


def test_preamble_and_unicode_are_preserved():
    source = "Café frontmatter\r\n\r\n# Introducción\r\nHola\r\n\r\n## Résumé\r\nÀ bientôt.\r\n"
    sections = split_sections(source)
    assert [s.heading for s in sections] == ["Preamble", "Introducción", "Résumé"]
    assert "".join(s.text for s in sections) == source
    patched, _ = apply_section_patch(source, _patch(
        source, "Introducción", "# Introducción\r\nBuenos días\r\n\r\n",
    ))
    assert patched.endswith("## Résumé\r\nÀ bientôt.\r\n")


def test_duplicate_headings_have_unique_ids_but_ambiguous_requests_are_refused():
    source = "# Introduction\nstart\n# Risks\nfirst\n# Risks\nsecond\n" + "x" * 6500
    matches = [s for s in split_sections(source) if s.heading == "Risks"]
    assert matches[0].section_id != matches[1].section_id
    with pytest.raises(SectionEditError, match="same heading"):
        select_edit_plan(source, [{"role": "user", "content": "Revise Risks"}])


def test_overlapping_heading_names_prefer_the_longer_exact_match():
    source = "# Summary\nshort\n# Executive Summary\n" + "x" * 7000
    plan = select_edit_plan(source, [{"role": "user", "content": "Refine Executive Summary"}])
    assert plan is not None
    assert [s.heading for s in plan.selected] == ["Executive Summary"]


def test_replacing_one_section_preserves_other_sections_byte_for_byte():
    patch = _patch(DOC, "Risks", "## Risks\n\nUpdated risk description.\n\n")
    result, summary = apply_section_patch(DOC, patch)
    assert result == DOC.replace("## Risks\n\nOld risk text.\n\n", "## Risks\n\nUpdated risk description.\n\n")
    assert summary == "Updated the requested section."


def test_same_level_heading_rename_is_allowed():
    updated, _ = apply_section_patch(DOC, _patch(
        DOC, "Risks", "## Risks and Mitigations\n\nUpdated.\n\n",
    ))
    assert "## Risks and Mitigations\n" in updated
    assert "## Timeline\n\nDo not change this schedule.\n" in updated


def test_two_valid_changes_apply_atomically():
    patch = _patch(DOC, "Scope", "## Scope\n\nExpanded.\n\n")
    patch["operations"].extend(_patch(
        DOC, "Risks", "## Risks\n\nReassessed.\n\n",
    )["operations"])
    updated, _ = apply_section_patch(DOC, patch)
    assert "Expanded." in updated and "Reassessed." in updated
    assert "Do not change this schedule." in updated


def test_invalid_second_operation_does_not_produce_a_partially_changed_draft():
    patch = _patch(DOC, "Scope", "## Scope\n\nExpanded.\n\n")
    second = _patch(DOC, "Risks", "## Risks\n\nReassessed.\n\n")["operations"][0]
    second["expected_sha256"] = "0" * 64
    patch["operations"].append(second)
    with pytest.raises(SectionEditError):
        apply_section_patch(DOC, patch)
    assert "Keep this scope text." in DOC


@pytest.mark.parametrize("key,value", [
    ("base_sha256", "0" * 64),
    ("base_sha256", "not-a-hash"),
    ("version", 2),
    ("version", True),
    ("summary", ""),
    ("summary", "y" * 501),
])
def test_invalid_envelope_fails_closed(key, value):
    patch = _patch(DOC, "Risks", "## Risks\nNew.\n\n")
    patch[key] = value
    with pytest.raises(SectionEditError):
        apply_section_patch(DOC, patch)


def test_extra_patch_properties_cannot_override_parser_policy():
    patch = _patch(DOC, "Risks", "## Risks\nNew.\n\n")
    patch["execute"] = "unsafe"
    with pytest.raises(SectionEditError, match="contract"):
        apply_section_patch(DOC, patch)


def test_section_hash_and_allowed_targets_must_match():
    patch = _patch(DOC, "Risks", "## Risks\nNew.\n\n")
    with pytest.raises(SectionEditError, match="unselected"):
        apply_section_patch(DOC, patch, allowed_section_ids={"other"})
    patch["operations"][0]["expected_sha256"] = "0" * 64
    with pytest.raises(SectionEditError, match="section has changed"):
        apply_section_patch(DOC, patch)


def test_duplicate_and_excessive_operations_are_rejected():
    patch = _patch(DOC, "Risks", "## Risks\nNew.\n\n")
    patch["operations"].append(dict(patch["operations"][0]))
    with pytest.raises(SectionEditError, match="only be changed once"):
        apply_section_patch(DOC, patch)
    patch["operations"] *= MAX_OPERATIONS + 1
    with pytest.raises(SectionEditError, match="one to three"):
        apply_section_patch(DOC, patch)


@pytest.mark.parametrize("replacement", [
    "",
    "Unstructured replacement\n",
    "### Wrong heading level\nContent\n\n",
    "## Risks\n\nUpdated\n\n## Injected new section\nInjected\n",
    "## Risks\n\nMissing separating newline",
    "## Risks\n\n" + "x" * (MAX_REPLACEMENT_CHARS + 1) + "\n",
])
def test_dangerous_or_truncated_replacements_are_rejected(replacement):
    with pytest.raises(SectionEditError):
        apply_section_patch(DOC, _patch(DOC, "Risks", replacement))


def test_headings_inside_replacement_code_fences_do_not_break_validation():
    code = "## Risks\n\n~~~markdown\n# Documentation example\n~~~\n\n"
    output, _ = apply_section_patch(DOC, _patch(DOC, "Risks", code))
    assert "Documentation example" in output


def test_noop_proposal_is_not_misrepresented_as_an_edit():
    unchanged = next(s.text for s in split_sections(DOC) if s.heading == "Risks")
    with pytest.raises(SectionEditError, match="no document changes"):
        apply_section_patch(DOC, _patch(DOC, "Risks", unchanged))


def test_very_large_document_without_a_named_section_fails_with_guidance():
    source = "# Scope\n" + "x" * 50000
    with pytest.raises(SectionEditError, match="Name a unique section heading"):
        select_edit_plan(source, [{"role": "user", "content": "Rewrite everything"}])


def test_oversized_target_section_is_not_sent_to_model():
    source = "# Background\n" + "x" * 30000 + "\n# Risks\n" + "y" * 15000
    with pytest.raises(SectionEditError, match="too large"):
        select_edit_plan(source, [{"role": "user", "content": "Revise Risks"}])


def test_real_outbound_edit_context_is_one_third_or_less_of_long_source():
    source = (
        "# Background\n" + "x" * 11000 + "\n"
        "# Financials\n" + "y" * 11000 + "\n"
        "# Risks\n" + "z" * 9000 + "\n"
        "# Roadmap\n" + "w" * 9000 + "\n"
    )
    plan = select_edit_plan(source, [{"role": "user", "content": "Revise the Risks section"}])
    assert plan is not None
    payload = json.dumps(patch_messages(plan))
    assert len(payload) < len(source) / 3
    assert "y" * 100 not in payload
    assert "w" * 100 not in payload
    assert "z" * 100 in payload


def test_artifact_agent_uses_structured_json_route_for_local_edit(monkeypatch):
    source = (
        "# Background\n" + "x" * 9000 + "\n\n"
        "# Risks\nOld risks.\n\n"
        "# Timeline\n" + "t" * 9000 + "\n"
    )

    def fake_structured(*, task, messages, schema, schema_name):
        assert schema == SECTION_PATCH_SCHEMA
        assert schema_name == "artifact_section_patch_v1"
        assert messages[0]["content"] == SECTION_PATCH_SYSTEM_PROMPT
        info = json.loads(messages[1]["content"])
        assert "x" * 100 not in messages[1]["content"]
        assert "t" * 100 not in messages[1]["content"]
        selected = info["selected_sections"]
        assert [section["heading"] for section in selected] == ["Risks"]
        return {
            "version": 1, "base_sha256": info["base_sha256"],
            "operations": [{
                "kind": "replace-section",
                "section_id": selected[0]["section_id"],
                "expected_sha256": selected[0]["expected_sha256"],
                "replacement_markdown": "# Risks\nNew risks.\n\n",
            }],
            "summary": "Revised the risk analysis.",
        }

    monkeypatch.setattr(structured, "respond_json", fake_structured)
    monkeypatch.setattr(api_provider, "chat", lambda **kwargs: pytest.fail("Full rewrite used"))
    updated, summary = ArtifactAgent().get_response(
        source, [{"role": "user", "content": "Improve Risks"}],
    )
    assert "New risks." in updated and "Old risks." not in updated
    assert "# Background\n" + "x" * 9000 in updated
    assert "# Timeline\n" + "t" * 9000 in updated
    assert "Revised the risk analysis." in summary


def test_short_artifact_retains_existing_full_rewrite_path(monkeypatch):
    calls = []
    monkeypatch.setattr(
        api_provider, "chat",
        lambda **kwargs: calls.append(kwargs) or {
            "message": {"content": "<artifact>Replacement</artifact> Done"}
        },
    )
    monkeypatch.setattr(structured, "respond_json", lambda **kwargs: pytest.fail("Wrong route"))
    result, message = ArtifactAgent().get_response(
        "# Risks\nold\n", [{"role": "user", "content": "Revise Risks"}],
    )
    assert (result, message) == ("Replacement", "Done")
    assert len(calls) == 1


def test_global_request_under_ceiling_retains_full_rewrite(monkeypatch):
    doc = "# Background\n" + "x" * 10000 + "\n"
    monkeypatch.setattr(
        api_provider, "chat",
        lambda **kwargs: {"message": {"content": "<artifact>New entire doc</artifact> Done"}},
    )
    monkeypatch.setattr(structured, "respond_json", lambda **kwargs: pytest.fail("Wrong route"))
    output, _ = ArtifactAgent().get_response(
        doc, [{"role": "user", "content": "Rework the whole document structure"}],
    )
    assert output == "New entire doc"


def test_oversized_model_json_is_rejected_after_provider_response():
    with pytest.raises(SectionEditError, match="oversized"):
        validate_structured_result_size({"blob": "X" * 75000})


def test_native_strict_provider_schema_closes_every_object():
    # OpenAI strict Structured Outputs requires all fields be mandatory and
    # additionalProperties: false for every object, including nested items.
    root = SECTION_PATCH_SCHEMA
    operation = root["properties"]["operations"]["items"]
    for obj in (root, operation):
        assert obj["type"] == "object"
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
