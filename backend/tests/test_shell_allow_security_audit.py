"""Adversarial consent-boundary tests for persisted workspace shell approvals."""

from __future__ import annotations

import pytest

from backend.harness.shell_policy import (
    analyze, effective_shell_for_rules, shell_allow_rule_candidates,
    shell_command_allowed, valid_shell_allow_rule,
)


def rule(command, workspace, *, shell="auto", profile="host"):
    return shell_allow_rule_candidates(
        command, shell=shell, workspace_root=workspace, profile=profile,
    )


def allowed(command, rules, workspace, *, shell="auto", profile="host"):
    return shell_command_allowed(
        command, rules, shell=shell, workspace_root=workspace, profile=profile,
    )


@pytest.mark.parametrize(
    "payload",
    [
        "ls ../other", "cat ../../secret", "ls /etc", "cat /tmp/example",
        "git -C ../ status", "git status --work-tree=../other",
        "git status --git-dir=/tmp/repo", "git status --global",
        "cat C:/Users/Public/file", "cat file:///etc/passwd",
        "echo hi && cd ..", "cd .. && pwd",
        "ls -I/etc", "git -Coutside status",
    ],
)
def test_path_escape_and_opaque_path_option_never_remembered(tmp_path, payload):
    assert rule(payload, tmp_path) == []


@pytest.mark.parametrize(
    "payload",
    [
        "echo hello; rm -rf other", "echo hello && sudo true",
        "echo hello | sh", "echo 'hello; rm -rf other'",
        'echo "hello; rm -rf other"', "r\\m -rf other",
        "echo $(whoami)", "echo " + chr(96) + "whoami" + chr(96), "echo $HOME",
        "echo hi > output", "echo hi # comment",
        "python -m pytest", "python -c whoami",
        "bash script.sh", "env FOO=1 git status",
        "git -c core.fsmonitor=true status",
        "@rm -rf output", "@del output", "busybox rm output",
    ],
)
def test_unsafe_shell_forms_never_remembered(tmp_path, payload):
    assert rule(payload, tmp_path) == []


def test_chains_do_not_inherit_single_command_grants(tmp_path):
    git_rule = rule("git status", tmp_path)
    test_rule = rule("npm test", tmp_path)
    assert git_rule and test_rule
    assert not allowed("git status && npm test", git_rule + test_rule, tmp_path)
    combined = rule("git status && npm test", tmp_path)
    assert allowed("git status && npm test", combined, tmp_path)
    assert not allowed("git status; npm test", combined, tmp_path)
    assert not allowed("git status && npm test --silent", combined, tmp_path)
    assert not allowed("git status && npm test", [], tmp_path)


def test_workspace_rebind_and_execution_profile_cannot_reuse_grants(tmp_path):
    workspace = tmp_path / "workspace"
    other = tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    host = rule("git status", workspace)
    assert allowed("git status", host, workspace)
    assert not allowed("git status", host, other)
    assert not allowed("git status", host, workspace, profile="isolated")
    isolated = rule("git status", workspace, profile="isolated")
    assert isolated and isolated != host
    assert isolated[0].split("\t")[1] == "sh"
    assert allowed("git status", isolated, workspace, profile="isolated")
    assert not allowed("git status", isolated, workspace, profile="host")
    assert effective_shell_for_rules("powershell", profile="isolated") == "sh"


def test_symlink_target_rechecked_on_every_use(tmp_path):
    inside = tmp_path / "inside.txt"
    outside = tmp_path.parent / "outside.txt"
    inside.write_text("inside")
    outside.write_text("outside")
    link = tmp_path / "link"
    try:
        link.symlink_to(inside)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unavailable")
    rules = rule("cat link", tmp_path)
    assert allowed("cat link", rules, tmp_path)
    link.unlink()
    link.symlink_to(outside)
    assert not allowed("cat link", rules, tmp_path)
    assert rule("cat link", tmp_path) == []


def test_paths_in_option_values_are_checked(tmp_path):
    outside = tmp_path.parent / "outside-config"
    outside.write_text("data")
    link = tmp_path / "config"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unavailable")
    assert rule("git status --work-tree=config", tmp_path) == []


def test_legacy_rules_and_malformed_v2_are_inert(tmp_path):
    current = rule("git status", tmp_path)
    assert current and valid_shell_allow_rule(current[0])
    for saved in (
        "bash\tgit status", "bash\t", "v2\tbash\thost\tgarbage\tgit status",
        current[0] + " && rm -rf output",
        current[0].replace("\thost\t", "\tinvalid\t"),
    ):
        assert not valid_shell_allow_rule(saved) or not allowed("git status", [saved], tmp_path)
    assert not allowed("git status", ["bash\tgit status"], tmp_path)
    assert not allowed("git status", current, tmp_path, shell="unknown-shell")


def test_unmatched_quotes_and_cmd_single_quotes_fail_closed(tmp_path):
    for shell in ("bash", "cmd"):
        plan = analyze('echo "unclosed & rm -rf other', shell=shell)
        assert plan.unsplittable and plan.is_dangerous
    cmd = analyze("echo 'text & rm -rf other", shell="cmd")
    assert cmd.dangerous == ["rm -rf other"]
    assert cmd.segments == ["echo 'text", "rm -rf other"]
    assert not rule("echo 'text & rm -rf other", tmp_path)
    bash = analyze("echo 'text & rm -rf other'", shell="bash")
    assert bash.segments == ["echo 'text & rm -rf other'"]
    assert not bash.is_dangerous


@pytest.mark.parametrize("payload", ["echo hi # comment", "env FOO=1 git status"])
def test_powershell_comments_and_env_wrappers_cannot_be_remembered(
    tmp_path, monkeypatch, payload,
):
    from backend.harness import shell_selection

    # Exercise both explicit PowerShell and auto selection on Windows,
    # even when the test itself runs on POSIX.
    monkeypatch.setattr(shell_selection, "effective_shell", lambda shell: "powershell")
    for shell in ("powershell", "auto"):
        safe_rule = rule("git status", tmp_path, shell=shell)
        assert safe_rule
        assert rule(payload, tmp_path, shell=shell) == []

        # Even a preexisting v2 rule with this exact unsafe payload must
        # not bypass a fresh approval.
        forged = safe_rule[0].rsplit("\t", 1)[0] + "\t" + payload
        assert valid_shell_allow_rule(forged)
        assert not allowed(payload, [forged], tmp_path, shell=shell)


def test_powershell_injection_is_not_remembered(tmp_path, monkeypatch):
    from backend.harness import shell_selection

    monkeypatch.setattr(shell_selection, "effective_shell", lambda shell: "powershell")
    assert rule("Get-Item README.md", tmp_path, shell="powershell")
    for command in (
        "Get-Item README.md; Remove-Item README.md",
        "Get-Item README.md | Invoke-Expression",
        "Get-Item README.md; & evil.ps1",
        'Write-Output "$(Remove-Item file)"',
        "Get-Item ../secret",
        "Get-Item -Path C:/",
        "Get-Item README.md" + chr(96) + "nRemove-Item file",
    ):
        assert rule(command, tmp_path, shell="powershell") == []


def test_cmd_at_prefix_and_powershell_aliases_are_dangerous():
    # cmd @ suppresses command echo, not the destructive operation.
    for command in ("@del file", "@rmdir /s build"):
        assert analyze(command, shell="cmd").is_dangerous
    for command in ("cp old new", "ni foo", "New-Item file", "sc foo"):
        assert analyze(command, shell="powershell").is_dangerous
