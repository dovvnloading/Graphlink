"""Review previews for workspace writes are faithful and do not touch disk."""

from __future__ import annotations

from backend.harness.loop import _approval_summary
from backend.providers.base import ToolCall


def preview(name, root, **arguments):
    call = ToolCall(id="pending", name=name, arguments=arguments)
    return _approval_summary(call, workspace_root=root)


def test_new_file_shows_complete_unmodified_content(tmp_path):
    body = "\n+this is literal content\n<script>not HTML</script>\n"
    summary = preview("fs.write", tmp_path, path="src/new.txt", content=body)

    assert summary == f"fs.write src/new.txt\n--- new file: src/new.txt (full content)\n{body}"
    assert not (tmp_path / "src" / "new.txt").exists()


def test_overwrite_shows_unified_diff_against_file_on_disk(tmp_path):
    target = tmp_path / "config.txt"
    target.write_bytes(b"enabled=false\nuntouched=yes\n")

    summary = preview(
        "fs.write", tmp_path, path="config.txt",
        content="enabled=true\nuntouched=yes\n",
    )

    assert summary.startswith("fs.write config.txt\n--- a/config.txt\n+++ b/config.txt\n@@")
    assert "-enabled=false\n" in summary
    assert "+enabled=true\n" in summary
    assert " untouched=yes\n" in summary
    assert target.read_text(encoding="utf-8") == "enabled=false\nuntouched=yes\n"


def test_edit_shows_whole_resulting_line_not_just_replacement_arguments(tmp_path):
    target = tmp_path / "module.py"
    target.write_bytes(b"prefix = 'before'  # context\nkeep = 1\n")

    summary = preview(
        "fs.edit", tmp_path, path="module.py",
        old_string="'before'", new_string="'after'",
    )

    assert summary.startswith("fs.edit module.py\n--- a/module.py\n+++ b/module.py\n@@")
    assert "-prefix = 'before'  # context\n" in summary
    assert "+prefix = 'after'  # context\n" in summary
    assert " keep = 1\n" in summary
    assert target.read_text(encoding="utf-8") == "prefix = 'before'  # context\nkeep = 1\n"


def test_preview_detects_newline_only_changes(tmp_path):
    (tmp_path / "line.txt").write_bytes(b"last")
    summary = preview("fs.write", tmp_path, path="line.txt", content="last\n")

    assert "-last\n\\ No newline at end of file\n" in summary
    assert "+last\n" in summary


def test_noop_write_explains_that_content_is_identical(tmp_path):
    (tmp_path / "same.txt").write_text("already here", encoding="utf-8")
    summary = preview("fs.write", tmp_path, path="same.txt", content="already here")
    assert summary == "fs.write same.txt\nNo content changes."


def test_unpreviewable_or_invalid_edit_never_claims_a_diff(tmp_path):
    (tmp_path / "dup.txt").write_text("same same", encoding="utf-8")
    (tmp_path / "binary.txt").write_bytes(b"\xff")

    ambiguous = preview(
        "fs.edit", tmp_path, path="dup.txt",
        old_string="same", new_string="other",
    )
    missing = preview(
        "fs.edit", tmp_path, path="absent.txt",
        old_string="old", new_string="new",
    )
    unreadable = preview("fs.write", tmp_path, path="binary.txt", content="new")
    unbound = preview("fs.write", None, path="new.txt", content="new")

    assert "Preview unavailable: old_string occurs 2 times" in ambiguous
    assert "Preview unavailable: Not a file" in missing
    assert "Preview unavailable:" in unreadable
    assert "Preview unavailable: No harness workspace is bound" in unbound
    assert "--- a/" not in ambiguous + missing + unreadable + unbound


def test_paths_outside_workspace_cannot_be_read_for_preview(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("do not disclose", encoding="utf-8")

    summary = preview("fs.write", root, path="../private.txt", content="new")

    assert "Preview unavailable:" in summary
    assert "outside this run's workspace" in summary
    assert "do not disclose" not in summary
    assert "--- a/" not in summary
