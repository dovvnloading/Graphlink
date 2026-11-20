"""PowerShell policy regression tests; does not switch the executor shell.

The shell argument opts into PowerShell analysis only. The default cmd/POSIX
path and fresh-approval contract continue to be covered by test_harness_h6.
"""

import pytest

from backend.harness.shell_policy import analyze, is_dangerous_command


@pytest.mark.parametrize("shell", ["powershell", "pwsh", "powershell.exe", "pwsh.exe"])
def test_powershell_chains_disclose_and_check_each_segment(shell):
    command = (
        "Get-ChildItem .; Remove-Item -Path ./build -Recurse -Force "
        "&& Get-Process | Stop-Process"
    )
    plan = analyze(command, shell=shell)
    assert not plan.unsplittable
    assert plan.segments == [
        "Get-ChildItem .",
        "Remove-Item -Path ./build -Recurse -Force",
        "Get-Process",
        "Stop-Process",
    ]
    assert plan.dangerous == [
        "Remove-Item -Path ./build -Recurse -Force",
        "Stop-Process",
    ]
    assert "! Remove-Item -Path ./build -Recurse -Force" in plan.disclosure()
    assert "! Stop-Process" in plan.disclosure()
    assert is_dangerous_command(command, shell=shell)


def test_newlines_pipeline_chaining_and_repeated_commands():
    plan = analyze(
        "Get-Item foo || Remove-Item foo\r\n"
        "Get-Process | Stop-Process; Stop-Process",
        shell="pwsh",
    )
    assert plan.segments == [
        "Get-Item foo", "Remove-Item foo", "Get-Process",
        "Stop-Process", "Stop-Process",
    ]
    assert plan.dangerous == ["Remove-Item foo", "Stop-Process"]


@pytest.mark.parametrize(
    ("command", "segments"),
    [
        (
            'Write-Output "semi; pipe| chain&& amp&"; Get-ChildItem',
            ['Write-Output "semi; pipe| chain&& amp&"', "Get-ChildItem"],
        ),
        (
            "Write-Output 'semi; and ''pipe|'''; Get-ChildItem",
            ["Write-Output 'semi; and ''pipe|'''", "Get-ChildItem"],
        ),
        (
            "Write-Output a\x60;b; Stop-Process -Id 123",
            ["Write-Output a\x60;b", "Stop-Process -Id 123"],
        ),
        (
            "Write-Output hi # ; Remove-Item x\nGet-Item x",
            ["Write-Output hi", "Get-Item x"],
        ),
    ],
)
def test_separators_in_literals_escapes_and_comments_do_not_create_commands(command, segments):
    assert analyze(command, shell="powershell").segments == segments


def test_powershell_ampersand_is_an_invocation_operator_or_background_boundary():
    invoked = analyze("& 'script.ps1'; Get-Item .", shell="powershell")
    assert invoked.segments == ["& 'script.ps1'", "Get-Item ."]
    assert invoked.dangerous == ["& 'script.ps1'"], "script execution must be reviewed"
    background = analyze("Get-Process & Stop-Process -Id 42", shell="powershell")
    assert background.segments == ["Get-Process", "Stop-Process -Id 42"]
    assert background.dangerous == ["Stop-Process -Id 42"]


@pytest.mark.parametrize(
    "command",
    [
        "Remove-Item -Recurse -Force .\\build",
        "ri -r -fo .\\build",
        "rm -r .\\build",
        "del .\\important.txt",
        "Stop-Process -Id 42 -Force",
        "spps -Id 42",
        "Stop-Service -Name some-service",
        "spsv -Name some-service",
        "Clear-Disk -Number 0",
        "Format-Volume -DriveLetter C",
        "Set-ExecutionPolicy Bypass",
        "Get-Content script.ps1 | iex",
        "Out-File -FilePath profile.ps1",
        "Rem\x60ove-Item -Recurse .\\build",
        "Microsoft.PowerShell.Management\\Remove-Item -Recurse .\\build",
        "pwsh -EncodedCommand YQBiAGMA",
    ],
)
def test_powershell_destructive_commands_and_aliases_are_dangerous(command):
    assert is_dangerous_command(command, shell="powershell")


@pytest.mark.parametrize(
    "command",
    [
        "Get-ChildItem -Recurse .",
        "Get-Process | Where-Object Name -Like 'python*'",
        "Write-Output 'Remove-Item -Recurse x'",
        "git status",
    ],
)
def test_read_only_powershell_commands_are_not_dangerous(command):
    assert not is_dangerous_command(command, shell="pwsh")


@pytest.mark.parametrize(
    "command",
    [
        'Write-Output "$(Remove-Item -Recurse .\\build)"',
        "ForEach-Object { Remove-Item -Recurse .\\build }",
        "Write-Output (Remove-Item -Recurse .\\build)",
        "Write-Output @' \nRemove-Item x\n'@",
        "Write-Output <# nested; commands #> x",
        "tool.exe --% literal; Remove-Item x",
        'Write-Output "unterminated',
        "Write-Output x\x60",
        "$x ?? Remove-Item x",
    ],
)
def test_nested_or_ambiguous_powershell_syntax_fails_closed(command):
    plan = analyze(command, shell="powershell")
    assert plan.unsplittable
    assert plan.is_dangerous
    assert plan.segments == [command]
    assert "reviewed as one unit" in plan.disclosure()


def test_legacy_cmd_posix_and_default_behavior_are_unchanged():
    # The legacy regex even splits inside quotes; changing that belongs to a
    # separate review, not a PowerShell-only change.
    command = 'echo "a;b" & rm -rf out'
    expected = ['echo "a', 'b"', "rm -rf out"]
    for shell in (None, "cmd", "cmd.exe", "posix", "sh", "bash", "zsh"):
        plan = analyze(command, shell=shell)
        assert plan.segments == expected
        assert plan.dangerous == ["rm -rf out"]
        assert not plan.unsplittable


def test_unrecognized_shell_is_reviewed_whole_rather_than_guessed():
    plan = analyze("Get-Item x; something else", shell="unknown-shell")
    assert plan.unsplittable and plan.is_dangerous
