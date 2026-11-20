"""Workspace/user SKILL.md discovery and on-demand loading for the harness."""

from __future__ import annotations

import asyncio

import pytest

from backend.harness import skills
from backend.harness.context import build_system_prompt
from backend.harness.tools_skills import register_workspace_tools
from backend.providers.base import ToolCall
from backend.tools import FS_READ, RunContext, ToolRegistry


def _write_skill(base, folder, *, name, description, body="## Procedure\nDo the work.\n"):
    package = base / folder
    package.mkdir(parents=True)
    text = f"---\nname: {name}\ndescription: {description}\n---\n{body}"
    (package / "SKILL.md").write_text(text, encoding="utf-8")
    return text


@pytest.fixture
def roots(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    user = tmp_path / "home" / ".graphlink" / "skills"
    user.mkdir(parents=True)
    monkeypatch.setattr(skills, "USER_SKILLS_DIR", user)
    return workspace, workspace / ".graphlink" / "skills", user


def test_prompt_lists_both_sources_without_eagerly_loading_skill_bodies(roots):
    workspace, local, user = roots
    _write_skill(local, "formatting", name="formatting", description="Format reports.", body="DO_NOT_INLINE_THIS_BODY")
    _write_skill(user, "testing", name="testing", description="Run the regression suite.", body="USER_SECRET_BODY")

    prompt = build_system_prompt(workspace, machine_context="machine facts")
    assert "workspace / formatting: Format reports." in prompt
    assert "user / testing: Run the regression suite." in prompt
    assert "skills.load" in prompt
    assert "DO_NOT_INLINE_THIS_BODY" not in prompt
    assert "USER_SECRET_BODY" not in prompt
    assert prompt == build_system_prompt(workspace, machine_context="machine facts")


def test_no_catalog_when_there_are_no_skills(roots):
    workspace, _, _ = roots
    assert "## Available skills" not in build_system_prompt(workspace, machine_context="machine")
    assert skills.discover_skills(workspace) == ()


def test_metadata_parses_quoted_and_folded_descriptions_without_leaking_body(roots):
    workspace, local, _ = roots
    pkg = local / "field-notes"
    pkg.mkdir(parents=True)
    (pkg / "SKILL.md").write_text(
        "---\nname: 'field-notes'\ndescription: >-\n  Collect samples and\n  annotate the results.\n---\n# Secret body\n",
        encoding="utf-8",
    )
    prompt = skills.skills_prompt(workspace)
    assert "workspace / field-notes: Collect samples and annotate the results." in prompt
    assert "Secret body" not in prompt


def test_load_returns_complete_text_and_resolves_name_collisions_by_source(roots):
    workspace, local, user = roots
    local_text = _write_skill(local, "one", name="audit", description="Workspace audit.", body="LOCAL END MARKER\n")
    user_text = _write_skill(user, "two", name="audit", description="Personal audit.", body="PERSONAL END MARKER\n")
    catalog = skills.skills_prompt(workspace)
    assert "workspace / audit: Workspace audit." in catalog
    assert "user / audit: Personal audit." in catalog
    assert skills.load_skill(workspace, source="workspace", name="audit") == local_text
    assert skills.load_skill(workspace, source="user", name="audit") == user_text


def test_load_tool_is_auto_approved_read_only_and_works_outside_workspace(roots):
    workspace, _, user = roots
    expected = _write_skill(user, "sample", name="sample", description="User scope.", body="Read this entire text.\n")
    registry = ToolRegistry()
    register_workspace_tools(registry, None, None)
    assert registry.scopes_for("skills.load") == frozenset({FS_READ})
    assert registry.approval_for("skills.load") == "auto"
    assert registry.parallel_safe_for("skills.load")

    async def must_not_ask(_call):
        raise AssertionError("a read-only skill load must not request approval")

    ctx = RunContext(granted_scopes=frozenset({FS_READ}), request_approval=must_not_ask)
    ctx.harness_workspace_dir = workspace

    async def load(source, name):
        return await registry.invoke(
            ToolCall(id="load", name="skills.load", arguments={"source": source, "name": name}), ctx,
        )

    loaded = asyncio.run(load("user", "sample"))
    assert not loaded.is_error and loaded.content == expected
    missing = asyncio.run(load("user", "missing"))
    assert missing.is_error and "not found" in missing.content
    traversal = asyncio.run(load("workspace", "../sample"))
    assert traversal.is_error and "valid source" in traversal.content
    bad_source = asyncio.run(load("other", "sample"))
    assert bad_source.is_error


def test_missing_untrusted_and_oversize_packages_are_not_read(roots):
    workspace, local, _ = roots
    (local / "invalid").mkdir(parents=True)
    (local / "invalid" / "SKILL.md").write_text("# Missing metadata\n", encoding="utf-8")
    _write_skill(local, "huge", name="huge", description="Long instructions.", body="z" * (128 * 1024))
    assert [entry.name for entry in skills.discover_skills(workspace)] == ["huge"]
    with pytest.raises(skills.SkillError, match="read limit"):
        skills.load_skill(workspace, source="workspace", name="huge")
    with pytest.raises(skills.SkillError, match="not found"):
        skills.load_skill(workspace, source="workspace", name="invalid")


def test_symlink_packages_and_symlinked_workspace_metadata_are_excluded(roots, tmp_path):
    workspace, local, user = roots
    _write_skill(user, "real", name="real", description="Real package.")
    local.mkdir(parents=True)
    try:
        (local / "linked-package").symlink_to(user / "real", target_is_directory=True)
        (local / "linked-file").mkdir()
        (local / "linked-file" / "SKILL.md").symlink_to(user / "real" / "SKILL.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert not any(item.source == "workspace" for item in skills.discover_skills(workspace))
    assert skills.load_skill(workspace, source="user", name="real").endswith("Do the work.\n")

    # A session's chosen workspace cannot make an external skill directory
    # visible just by symlinking its .graphlink parent outside the root.
    other = tmp_path / "other"
    other.mkdir()
    (other / ".graphlink").symlink_to(user.parent, target_is_directory=True)
    assert not any(item.source == "workspace" for item in skills.discover_skills(other))


def test_skill_disappearance_after_prompt_is_an_error_not_a_fallback(roots):
    workspace, local, _ = roots
    _write_skill(local, "disappearing", name="task", description="Temporary task.")
    assert "workspace / task" in skills.skills_prompt(workspace)
    (local / "disappearing" / "SKILL.md").unlink()
    with pytest.raises(skills.SkillError, match="not found"):
        skills.load_skill(workspace, source="workspace", name="task")
