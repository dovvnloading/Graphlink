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

# Only syntax we can disclose correctly is segmented; ambiguous shell
# grammar falls back to one dangerous/unsplittable approval prompt.
_POWERSHELL_NAMES = frozenset({"powershell", "powershell.exe", "pwsh", "pwsh.exe"})

# Constructs whose contents we decline to parse - see the module docstring.
# Unrecognized expansion, escaping and grouping syntax fails closed below.

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
    "cp", "cpi", "ni", "new-item", "sc", "cli", "clp", "clc",
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
        # cmd's leading @ modifier suppresses command echo but does NOT
        # change which program runs (@del still deletes files).
        if not tokens and lowered.startswith("@"):
            lowered = lowered[1:]
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




def _split_simple_shell(text: str, *, cmd: bool) -> list[str] | None:
    """Disclose real top-level statements, not separators inside quotes.

    This is intentionally NOT a full Bash/cmd parser. Expansion, escapes,
    grouping and comments have subtle shell-specific semantics and are
    conservatively treated as unsplittable (fresh approval, no saved rule).
    """
    segments: list[str] = []
    start = 0
    quote: str | None = None
    i = 0
    while i < len(text):
        char = text[i]
        following = text[i + 1:i + 2]
        if quote == "'":
            if char == "'":
                quote = None
            i += 1
            continue
        if quote == '"':
            if char == '"':
                quote = None
            elif char in ("$", chr(96), chr(92)) and not cmd:
                return None  # Bash expansion, substitution, or escaping
            elif char in "%!^" and cmd:
                return None  # cmd expansion/escaping in quotes
            i += 1
            continue
        if char == '"' or (char == "'" and not cmd):
            # Quoted command names can disguise rm as "r"m or 'rm'.
            # cmd does NOT treat single quotes as quoting characters.
            before = text[start:i]
            if (not before.strip() or
                    (not before[-1].isspace() and len(before.split()) == 1)):
                return None
            quote = char
            i += 1
            continue
        if char in "(){}" or (char in (chr(92), chr(96), "$", "#") and not cmd):
            return None
        if cmd and char in "^%!":
            return None
        if text.startswith(("<(", ">("), i):
            return None
        is_separator = char in "&|\n" or (not cmd and char == ";")
        if char == "\r":
            if following != "\n":
                return None
            is_separator = True
        if is_separator:
            part = text[start:i].strip()
            if not part:
                return None
            segments.append(part)
            size = 2 if ((char in "&|" and following == char)
                         or (char == "\r" and following == "\n")) else 1
            i += size
            start = i
            continue
        i += 1
    if quote is not None:
        return None
    tail = text[start:].strip()
    if not tail:
        return None
    segments.append(tail)
    return segments


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
        segments = _split_simple_shell(text, cmd=shell_name in {"cmd", "cmd.exe"})
        if segments is None:
            plan.unsplittable = True
        else:
            plan.segments = segments
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


# A remembered approval is a scoped EXACT command, not a prefix. Expanding
# arguments or composing separately-approved segments creates a new command
# requiring consent. Legacy shell<TAB>prefix entries are deliberately inert.
MAX_SHELL_ALLOW_RULES = 100
_SHELL_RULE_SEPARATOR = "\t"
_SHELL_RULE_VERSION = "v2"
_MAX_SAVED_COMMAND_CHARS = 256
_PLAIN_SHELL_WORDS = re.compile(
    r"[A-Za-z0-9_./:@=+,-]+(?: [A-Za-z0-9_./:@=+,-]+)*\Z"
)
_NON_PERSISTABLE_PROGRAMS = frozenset({
    "env", "eval", "exec", "source", ".", "command", "builtin",
    "sh", "bash", "zsh", "fish", "python", "python3", "node", "npx",
    "ruby", "perl", "php", "lua", "cmd", "powershell", "pwsh",
    "xargs", "find", "busybox", "cd", "pushd", "popd", "set-location", "sl",
    "call", "start", "set", "export", "alias",
})
_NON_PERSISTABLE_OPTIONS = frozenset({
    "--global", "--system", "--exec-path", "--config-env", "-c",
})


def effective_shell_for_rules(shell: str, *, profile: str = "host") -> str:
    from backend.harness.shell_selection import VALID_SHELLS, effective_shell

    # Unrecognized preferences may not silently inherit the auto shell's rules.
    if shell not in VALID_SHELLS:
        return "<unknown-shell>"

    # Isolated shell.exec always invokes /bin/sh inside the container, even
    # when the host preference is PowerShell. Its grammar and grants must be
    # analyzed for the interpreter that ACTUALLY receives the command.
    return "sh" if profile == "isolated" else effective_shell(shell)


def _workspace_binding(workspace_root: Path | None, profile: str) -> tuple[Path, str] | None:
    if not isinstance(workspace_root, Path) or profile not in {"host", "isolated"}:
        return None
    try:
        root = workspace_root.resolve(strict=True)
        if not root.is_dir():
            return None
        digest = hashlib.sha256(
            os.fsencode(os.path.normcase(str(root)))
        ).hexdigest()[:32]
    except (OSError, ValueError):
        return None
    return root, digest


def _safe_saved_segment(segment: str, root: Path) -> bool:
    """Small literal-token subset; no interpretation or path escape."""
    if not _PLAIN_SHELL_WORDS.fullmatch(segment):
        return False
    words = segment.split(" ")
    tokens = _tokens(segment)
    if not tokens or tokens[0] in _NON_PERSISTABLE_PROGRAMS:
        return False
    # A program specified by a path or a leading assignment is mutable
    # outside the shell lookup and not suitable for a durable approval.
    if "/" in words[0] or words[0].startswith("@") or re.match(r"[A-Za-z_][A-Za-z0-9_]*=", words[0]):
        return False
    from backend.harness.workspace import WorkspaceError, resolve_under_root

    for word in words:
        lowered = word.lower()
        if (lowered in _NON_PERSISTABLE_OPTIONS
                or any(lowered.startswith(option + "=")
                       for option in _NON_PERSISTABLE_OPTIONS)
                or re.fullmatch(r"-[A-Za-z0-9]{2,}", word)):
            # Includes attached short-option values such as -Coutside,
            # whose path semantics cannot be inferred from the token.
            return False
        if (".." in word or "://" in word or re.search(r"[A-Za-z]:/", word)
                or word.startswith("/") or re.match(r"-[A-Za-z]+/", word)):
            return False
        for value in word.split("="):
            if not value or value.startswith("-"):
                continue
            if value.startswith("/"):
                return False
            try:
                resolve_under_root(root, value)
            except (WorkspaceError, OSError, ValueError):
                # Re-resolve on EVERY invocation: a once-safe symlink may
                # now point outside the workspace.
                return False
    return True


def valid_shell_allow_rule(rule: object) -> bool:
    """Validate the versioned storage shape without trusting session data."""
    if not isinstance(rule, str):
        return False
    fields = rule.split(_SHELL_RULE_SEPARATOR)
    return (
        len(fields) == 5
        and fields[0] == _SHELL_RULE_VERSION
        and fields[1] in {"bash", "git-bash", "cmd", "powershell", "sh"}
        and fields[2] in {"host", "isolated"}
        and bool(re.fullmatch(r"[a-f0-9]{32}", fields[3]))
        and 0 < len(fields[4]) <= _MAX_SAVED_COMMAND_CHARS
        and not any(char in fields[4] for char in "\r\n\t")
    )


def shell_allow_rule_candidates(
    command: str, *, shell: str = "auto",
    workspace_root: Path | None = None, profile: str = "host",
) -> list[str]:
    """Offer ONE exact rule for a completely vetted command in this root.

    Each segment must pass the literal grammar and path test, but the
    operator/chain itself is part of the identity. No prefix widening,
    cross-command composition, rebind, or isolated-to-host reuse.
    """
    bound = _workspace_binding(workspace_root, profile)
    if bound is None or not isinstance(command, str):
        return []
    root, digest = bound
    text = command.strip()
    if not text or len(text) > _MAX_SAVED_COMMAND_CHARS or "\n" in text or "\r" in text:
        return []
    selected = effective_shell_for_rules(shell, profile=profile)
    plan = analyze(text, shell="bash" if selected == "git-bash" else selected)
    if plan.is_dangerous or not plan.segments:
        return []
    if not all(_safe_saved_segment(segment, root) for segment in plan.segments):
        return []
    return [_SHELL_RULE_SEPARATOR.join(
        (_SHELL_RULE_VERSION, selected, profile, digest, text)
    )]


def shell_command_allowed(
    command: str, rules: list[str], *, shell: str = "auto",
    workspace_root: Path | None = None, profile: str = "host",
) -> bool:
    """Only an exact, versioned rule for THIS root/profile skips a prompt."""
    if not isinstance(rules, list):
        return False
    candidates = shell_allow_rule_candidates(
        command, shell=shell, workspace_root=workspace_root, profile=profile,
    )
    return bool(candidates and any(
        valid_shell_allow_rule(rule) and rule == candidates[0] for rule in rules
    ))


def requires_fresh_shell_approval(tool_name: str, arguments: dict) -> bool:
    """Fail closed for commands with effects a shell classifier cannot prove.

    A tool-name session grant alone never bypasses execution approval.
    shell.exec may instead use an explicit node-scoped, exact command rule
    bound to the current workspace and execution profile.
    shell.session start/write always require fresh approval.
    Read/list/stop retain the less intrusive existing grant behavior.
    """
    if tool_name == "shell.exec":
        return True
    if tool_name == "shell.session":
        return str(arguments.get("action") or "").strip().lower() in {"start", "write"}
    return False
