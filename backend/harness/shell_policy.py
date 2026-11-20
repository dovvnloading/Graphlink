"""Shell command policy (PLAN-2026-08-24 §2.4).

Three independent jobs, all purely analytical - this module never runs
anything, so it is testable against strings alone:

1. **Segmentation.** A chained command is split on shell separators so the
   approval prompt can DISCLOSE each thing that will actually run, rather
   than showing one opaque line whose tail a human skims past. The plan's
   rule is "every segment must independently pass; one denied segment
   kills the whole command" - our approval model already satisfies the
   second half structurally (the tool is approved or denied as a unit),
   so what segmentation buys here is honest disclosure plus per-segment
   dangerous-command detection.

2. **The dangerous list.** Commands whose blast radius is not recoverable
   by "undo" - recursive deletes, force pushes, process kills, disk
   writes, privilege escalation, curl-pipe-to-shell. These force a fresh
   approval prompt every time, defeating the fingerprint memo that would
   otherwise let an identical repeat through silently (ToolRegistry's
   `always_reprompt` hook).

UNSPLITTABLE CONSTRUCTS are the load-bearing subtlety. Command
substitution (`$(...)`, backticks), process substitution (`<(...)`), and
subshells (`(...)`) can contain arbitrary nested commands whose text a
naive top-level split would either miss entirely or tear in half. Rather
than pretending to parse a shell grammar we do not implement, this module
detects those constructs and reports the command as UNSPLITTABLE: the
caller then prompts for the whole thing as one unit AND treats it as
dangerous-by-default, because we cannot prove what is inside it. That is
the conservative direction - the alternative (splitting badly and
approving a segment whose real content we misread) is exactly the failure
this design avoids.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# Top-level separators, longest-first so `&&` is never read as two `&`
# and `||` is never read as two `|`. The bare `&` is in the list because
# it chains on BOTH shells this app spawns: it is cmd.exe's ordinary
# sequential separator (`build & rm -rf out`) and POSIX sh's background
# operator, so a command after it runs either way - and a separator this
# module does not know about is a segment whose contents are never
# disclosed and never dangerous-checked, which is exactly the "dangerous
# tail hiding behind a benign head" this module exists to prevent.
# This regex remains the legacy cmd/POSIX behavior; PowerShell is opt-in.
_SEPARATOR_RE = re.compile(r"(\|\||&&|;|\||&|\n)")
_POWERSHELL_NAMES = frozenset({"powershell", "powershell.exe", "pwsh", "pwsh.exe"})

# Constructs whose contents we decline to parse - see the module docstring.
_UNSPLITTABLE_MARKERS = ("$(", "`", "<(", ">(")

# First-word program names that always re-prompt. Kept deliberately short:
# every entry is a command whose damage is not undoable from inside the app.
_DANGEROUS_PROGRAMS = frozenset({
    "rm", "rmdir", "del", "erase", "rd",
    "shred", "srm",
    "dd", "mkfs", "fdisk", "diskpart", "format",
    "kill", "pkill", "killall", "taskkill",
    "shutdown", "reboot", "halt", "poweroff",
    "sudo", "doas", "runas", "su",
    "chown", "chmod", "icacls", "takeown", "attrib",
    "mv", "move", "ren", "rename",
    "curl", "wget", "iwr", "invoke-webrequest",
    # Shell builtins, dispatchers and wrappers can execute a second command
    # that a first-word-only classifier would never see.
    "eval", "exec", "source", "command", "builtin", "call", "start",
    "cd", "chdir", "pushd", "popd", "env", "xargs", "find", "busybox",
    "parallel", "nohup", "setsid", "nice",
})

# Multi-word forms where the FIRST word alone is harmless. Matched against
# the segment's leading tokens, lowercased.
_DANGEROUS_PHRASES = (
    ("git", "push"),
    ("git", "reset"),
    ("git", "clean"),
    ("git", "checkout", "--"),
    ("npm", "publish"),
    ("pip", "uninstall"),
    ("docker", "rm"),
    ("docker", "rmi"),
    ("docker", "system"),
)



# PowerShell cmdlets and aliases with irreversible side effects. The shared
# list above still applies (including git push, rm, kill, etc.). Do not rely
# on a -Recurse or -Force flag: Remove-Item without those flags can delete a
# file too, and aliases such as ri/rm/del have the same effect.
_POWERSHELL_DANGEROUS_PROGRAMS = frozenset({
    "remove-item", "ri", "remove-itemproperty", "clear-item",
    "clear-itemproperty", "clear-content", "clear-recyclebin",
    "set-content", "out-file", "set-item", "set-itemproperty",
    "copy-item", "move-item", "rename-item", "mi", "si", "sp",
    "stop-process", "spps", "stop-service", "spsv", "restart-service",
    "stop-computer", "restart-computer", "stop-job", "remove-job",
    "remove-service", "remove-psdrive",
    "clear-disk", "format-volume", "initialize-disk",
    "remove-partition", "set-acl", "set-executionpolicy",
    "invoke-expression", "iex", "invoke-command", "icm",
    "start-process", "saps", "start-job",
    "powershell", "pwsh", "cmd",
})

@dataclass
class ShellPlan:
    """The analysis of one `shell.exec` command string."""

    command: str
    segments: list[str] = field(default_factory=list)
    unsplittable: bool = False
    unsplittable_note: str | None = None
    # Segments that tripped the dangerous list, in order, deduplicated.
    dangerous: list[str] = field(default_factory=list)

    @property
    def is_dangerous(self) -> bool:
        # An unsplittable command is dangerous BY DEFAULT: we could not read
        # what is inside it, so we cannot claim it is safe.
        return self.unsplittable or bool(self.dangerous)

    def disclosure(self) -> str:
        """The human-facing body of the approval prompt. One line per thing
        that will run, dangerous ones flagged inline - so the reason a
        prompt reappeared is visible in the prompt itself."""
        lines: list[str] = []
        if self.unsplittable:
            lines.append(self.unsplittable_note or (
                "! contains command substitution or a subshell - "
                "reviewed as one unit, contents not parsed"
            ))
        for segment in self.segments:
            marker = "!" if segment in self.dangerous else " "
            lines.append(f"{marker} {segment}")
        if self.dangerous:
            lines.append("")
            lines.append(
                "! flagged commands are not undoable - this prompt reappears "
                "every time, even for an identical repeat."
            )
        return "\n".join(lines)


def _tokens(segment: str) -> list[str]:
    """Leading whitespace-split tokens, lowercased, with common wrappers
    stripped so `env FOO=1 rm -rf x` and `/usr/bin/rm` both resolve to the
    real program name."""
    raw = segment.strip().split()
    tokens: list[str] = []
    for token in raw:
        lowered = token.lower()
        # Skip leading env assignments (FOO=bar) and the `env` wrapper.
        if not tokens and (lowered == "env" or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token)):
            continue
        # Normalize a path-qualified program to its basename: /usr/bin/rm,
        # C:\Windows\System32\del.exe, ./rm all name the same program.
        if not tokens:
            lowered = re.split(r"[\\/]", lowered)[-1]
            if lowered.endswith(".exe"):
                lowered = lowered[: -len(".exe")]
        tokens.append(lowered)
    return tokens


def _is_dangerous(segment: str) -> bool:
    tokens = _tokens(segment)
    if not tokens:
        return False
    if tokens[0] in _DANGEROUS_PROGRAMS:
        return True
    # Flag destructive subcommands even when a program option precedes the
    # verb (e.g. git -C other-repo push). A previously allowed bare "git"
    # prefix must never authorize a force push via option reordering.
    subcommands = {
        "git": {"push", "reset", "clean"},
        "npm": {"publish"},
        "pip": {"uninstall"},
        "docker": {"rm", "rmi", "system"},
    }
    if any(word in subcommands.get(tokens[0], ()) for word in tokens[1:]):
        return True
    if tokens[0] == "git" and "checkout" in tokens and "--" in tokens:
        return True
    for phrase in _DANGEROUS_PHRASES:
        if tokens[: len(phrase)] == list(phrase):
            return True
    # A pipe INTO a shell is the classic remote-code pattern; the pipe
    # itself is a separator so each side is its own segment, and a bare
    # `sh`/`bash` segment reading stdin is the tell.
    if tokens[0] in ("sh", "bash", "zsh", "powershell", "pwsh", "cmd") and len(tokens) == 1:
        return True
    return False



def _split_powershell(text: str) -> list[str] | None:
    """Split PowerShell top-level statements/pipelines for approval.

    PowerShell's backtick escapes separators; its & is both a call operator
    and (after a pipeline) a background-job operator. Quoted arguments are
    opaque to segmentation. We deliberately do NOT parse nested executable
    constructs (scriptblocks, expressions, expandable-string subexpressions,
    here-strings, block comments, or stop-parsing): fail closed instead of
    incorrectly claiming their inner commands were separately checked.
    """
    segments: list[str] = []
    start = 0
    quote: str | None = None
    i = 0
    while i < len(text):
        char = text[i]
        following = text[i + 1] if i + 1 < len(text) else ""

        if quote == "'":
            if char == "'":
                if following == "'":  # PowerShell single-quote escaping
                    i += 2
                    continue
                quote = None
            i += 1
            continue

        if char == "\x60":  # escapes quotes, pipes, semicolons, newlines
            if not following:
                return None
            i += 3 if following == "\r" and text[i + 2:i + 3] == "\n" else 2
            continue

        if quote == '"':
            # Double-quoted strings are expandable: "$( ... )" executes code.
            if text.startswith("$(", i):
                return None
            if char == '"':
                if following == '"':  # doubled quotes inside a string
                    i += 2
                    continue
                quote = None
            i += 1
            continue

        if char in ("'", '"'):
            quote = char
            i += 1
            continue

        if char in "(){}" or text.startswith(("<#", "@'", '@"', "??"), i):
            return None
        if text.startswith("--%", i) and (i == 0 or text[i - 1].isspace()):
            return None

        # A line comment terminates the current command, but any separators
        # in the comment body must not invent more commands for approval.
        if char == "#" and (i == 0 or text[i - 1].isspace() or text[i - 1] in ";|&"):
            before_comment = text[start:i].strip()
            if before_comment:
                segments.append(before_comment)
            newline = text.find("\n", i)
            if newline < 0:
                start = len(text)
                break
            i = newline + 1
            start = i
            continue

        separator_length = 0
        if char in ";\r\n|":
            separator_length = 2 if (
                (char == "|" and following == "|") or
                (char == "\r" and following == "\n")
            ) else 1
        elif char == "&":
            if following == "&":  # PowerShell 7 pipeline-chain operator
                separator_length = 2
            elif text[start:i].strip():
                # A leading "& command" invokes a command, not a separator.
                # A trailing "&" backgrounds an already-started pipeline.
                separator_length = 1

        if separator_length:
            segment = text[start:i].strip()
            if segment:
                segments.append(segment)
            i += separator_length
            start = i
            continue
        i += 1

    if quote is not None:
        return None
    tail = text[start:].strip()
    if tail:
        segments.append(tail)
    return segments


def _is_dangerous_powershell(segment: str) -> bool:
    # "& <script>" and ". <script>" can invoke arbitrary script content or
    # variable-held commands. Neither is a harmless standalone separator.
    if segment.lstrip().startswith("&") or re.match(r"^\s*\.\s+", segment):
        return True

    # Backticks can hide letters in cmdlet names: Rem-ove-Item can be
    # written with an escape in the middle. Unescape before matching names.
    unescaped = re.sub(r"\x60\r?\n", "", segment)
    unescaped = re.sub(r"\x60(.)", r"\1", unescaped)
    tokens = _tokens(unescaped)
    return (
        _is_dangerous(unescaped)
        or bool(tokens and tokens[0] in _POWERSHELL_DANGEROUS_PROGRAMS)
    )


def analyze(command: str, *, shell: str | None = None) -> ShellPlan:
    """Segment `command` and flag its dangerous parts. Never raises: a
    string this cannot make sense of comes back as one unsplittable
    segment, which the caller treats as dangerous.

    Request PowerShell grammar with `shell="powershell"` or `shell="pwsh"`.
    Omitted shell and explicit cmd/POSIX shells preserve the old policy.
    """
    text = (command or "").strip()
    plan = ShellPlan(command=text)
    if not text:
        return plan

    # The default is deliberately unchanged: shell.exec still uses cmd.exe
    # on Windows and /bin/sh on POSIX. PowerShell analysis is explicitly
    # requested by callers that actually execute PowerShell.
    shell_name = (shell or "").strip().lower()
    dangerous_check = _is_dangerous
    if shell_name in _POWERSHELL_NAMES:
        segments = _split_powershell(text)
        dangerous_check = _is_dangerous_powershell
        if segments is None:
            plan.unsplittable = True
            plan.unsplittable_note = (
                "! contains nested or ambiguous PowerShell syntax - "
                "reviewed as one unit, contents not parsed"
            )
        else:
            plan.segments = segments
    elif shell_name in {"", "cmd", "cmd.exe", "posix", "sh", "bash", "zsh"}:
        # We split only an unquoted, expansion-free subset. Regex splitting
        # inside quotes, escaped metacharacters or shell comments fabricates
        # commands that do not execute and can miss the real dangerous tail.
        # A full shell grammar is out of scope: display the entire original
        # text and require fresh consent instead of claiming parsed safety.
        if any(marker in text for marker in _UNSPLITTABLE_MARKERS) or any(
            char in text for char in ('"', "'", "\\", "$", "%", "^", "!", "(", ")", "{", "}", "<", ">", "#", "\r", "\t", "*", "?", "[", "]", "~")
        ):
            plan.unsplittable = True
            plan.unsplittable_note = (
                "! contains quoting, expansion or ambiguous shell syntax - "
                "reviewed as one unit, contents not parsed"
            )
        else:
            segments = [part.strip() for part in _SEPARATOR_RE.split(text)]
            # The capturing group retains separators; drop them here.
            plan.segments = [
                seg for seg in segments
                if seg and not _SEPARATOR_RE.fullmatch(seg)
            ]
        dangerous_check = _is_dangerous
    else:
        # Do not silently apply the wrong grammar to a new shell.
        plan.unsplittable = True

    if plan.unsplittable:
        plan.segments = [text]
        plan.dangerous = [text]
        return plan

    seen: set[str] = set()
    for segment in plan.segments:
        if dangerous_check(segment) and segment not in seen:
            seen.add(segment)
            plan.dangerous.append(segment)
    return plan


def is_dangerous_command(command: str, *, shell: str | None = None) -> bool:
    """The `always_reprompt` predicate ToolRegistry calls per invocation."""
    return analyze(command, shell=shell).is_dangerous


# A remembered approval is narrower than a normal, once-only shell prompt:
# it covers one EXACT, conservatively classified command segment, never a
# prefix whose unreviewed suffix could change flags, paths or interpreters.
# Only the backend may create in-memory rules. Graph/session files cannot
# attest to an operator's approval and MUST NOT restore these grants.
MAX_SHELL_ALLOW_RULES = 100
_SHELL_RULE_SEPARATOR = "\t"
# These forms have no caller-selected file operands, redirects, shell scripts,
# working-directory changes or interpreter flags. Shell commands outside this
# small set can still run after fresh approval; they just cannot be remembered.
# External binaries may be replaced via PATH, the workspace, or repo hooks.
# Only established shell builtins/cmdlets qualify for unattended reuse.
_POWERSHELL_READ_ONLY = frozenset({
    "get-location", "get-childitem", "get-childitem .",
    "get-item .", "get-process", "pwd", "ls", "ls .",
})
_PLAIN_SEGMENT_RE = re.compile(r"[A-Za-z0-9_./:@=+,-]+(?: [A-Za-z0-9_./:@=+,-]+)*")
_PLAIN_CHAIN_RE = re.compile(r"[A-Za-z0-9_./:@=+,\-;|& \n]+")


def effective_shell_for_rules(shell: str, *, profile: str = "host") -> str:
    # Isolated execution is ALWAYS /bin/sh -c inside a Linux container, even
    # if a Windows node is configured to use PowerShell or cmd on Host.
    if profile == "isolated":
        return "sh"
    from backend.harness.shell_selection import effective_shell

    return effective_shell(shell)


def _rule_scope(shell: str, profile: str, workspace_root: Path | None) -> str | None:
    """Bind consent to the real cwd, execution profile and interpreter.

    Missing/invalid roots fail closed; a workspace rebind or Host/Isolated
    switch never inherits a prior permission. The hash is an identifier, NOT
    an authentication tag; serialized rules are discarded on load.
    """
    if profile not in {"host", "isolated"} or workspace_root is None:
        return None
    try:
        root = Path(workspace_root).resolve(strict=True)
        if not root.is_dir():
            return None
    except (OSError, ValueError, TypeError, RuntimeError):
        return None
    selected = effective_shell_for_rules(shell, profile=profile)
    digest = hashlib.sha256(os.fsencode(os.path.normcase(str(root)))).hexdigest()[:32]
    return f"v2:{profile}:{selected}:{digest}"


def _rememberable(segment: str, selected: str) -> bool:
    if len(segment) > 256 or not _PLAIN_SEGMENT_RE.fullmatch(segment):
        return False
    normalized = segment.lower()
    # External commands may invoke changed binaries, git hooks or configured
    # helpers. Exact text alone is not proof that the program is read-only.
    if selected in {"bash", "git-bash", "sh"}:
        return normalized == "pwd" or normalized == "echo" or normalized.startswith("echo ")
    if selected == "cmd":
        return normalized in {"dir", "dir .", "echo"} or normalized.startswith("echo ")
    if selected == "powershell":
        return (
            normalized in _POWERSHELL_READ_ONLY
            or normalized in {"echo", "write-output"}
            or normalized.startswith(("echo ", "write-output "))
        )
    return False


def shell_allow_rule_candidates(
    command: str, *, shell: str = "auto", profile: str = "host",
    workspace_root: Path | None = None,
) -> list[str]:
    """Produce exact, workspace/profile/shell-bound in-memory grants.

    The splitter is only a *disclosure* aid, not a security-grade shell
    parser. For persisted consent, reject unfamiliar syntax and commands
    with arbitrary path operands or execution side effects. No root means
    no grant: caller must supply the run's already-bound workspace.
    """
    scope = _rule_scope(shell, profile, workspace_root)
    if (scope is None or not isinstance(command, str) or len(command) > 4000
            or not _PLAIN_CHAIN_RE.fullmatch(command)):
        return []
    selected = effective_shell_for_rules(shell, profile=profile)
    plan = analyze(command, shell="bash" if selected == "git-bash" else selected)
    if plan.is_dangerous or not plan.segments:
        return []
    if not all(_rememberable(segment, selected) for segment in plan.segments):
        return []
    return list(dict.fromkeys(
        f"{scope}{_SHELL_RULE_SEPARATOR}{segment}" for segment in plan.segments
    ))


def shell_command_allowed(
    command: str, rules: list[str], *, shell: str = "auto",
    profile: str = "host", workspace_root: Path | None = None,
) -> bool:
    """Only bypass when EVERY independently safe segment is an exact grant."""
    candidates = shell_allow_rule_candidates(
        command, shell=shell, profile=profile, workspace_root=workspace_root,
    )
    return bool(candidates and isinstance(rules, list) and all(
        candidate in rules for candidate in candidates
    ))


def requires_fresh_shell_approval(tool_name: str, arguments: dict) -> bool:
    """Fail closed for commands with effects a shell classifier cannot prove.

    A tool-name session grant alone never bypasses execution approval.
    shell.exec may instead use an exact in-memory command grant bound to
    the workspace root, execution profile and interpreter, after validating
    every segment with shell_command_allowed().
    shell.session start/write always require fresh approval.
    Read/list/stop retain the less intrusive existing grant behavior.
    """
    if tool_name == "shell.exec":
        return True
    if tool_name == "shell.session":
        return str(arguments.get("action") or "").strip().lower() in {"start", "write"}
    return False
