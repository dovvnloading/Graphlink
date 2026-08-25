"""Shell command policy (PLAN-2026-08-24 §2.4).

Two independent jobs, both purely analytical - this module never runs
anything, so it is exhaustively testable against strings alone:

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

import re
from dataclasses import dataclass, field

# Top-level separators, longest-first so `&&` is never read as two `&`.
_SEPARATOR_RE = re.compile(r"(\|\||&&|;|\||\n)")

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


@dataclass
class ShellPlan:
    """The analysis of one `shell.exec` command string."""

    command: str
    segments: list[str] = field(default_factory=list)
    unsplittable: bool = False
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
            lines.append(
                "! contains command substitution or a subshell - "
                "reviewed as one unit, contents not parsed"
            )
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
    for phrase in _DANGEROUS_PHRASES:
        if tokens[: len(phrase)] == list(phrase):
            return True
    # A pipe INTO a shell is the classic remote-code pattern; the pipe
    # itself is a separator so each side is its own segment, and a bare
    # `sh`/`bash` segment reading stdin is the tell.
    if tokens[0] in ("sh", "bash", "zsh", "powershell", "pwsh", "cmd") and len(tokens) == 1:
        return True
    return False


def analyze(command: str) -> ShellPlan:
    """Segment `command` and flag its dangerous parts. Never raises: a
    string this cannot make sense of comes back as one unsplittable
    segment, which the caller treats as dangerous."""
    text = (command or "").strip()
    plan = ShellPlan(command=text)
    if not text:
        return plan

    if any(marker in text for marker in _UNSPLITTABLE_MARKERS):
        plan.unsplittable = True
        plan.segments = [text]
        plan.dangerous = [text]
        return plan

    segments = [part.strip() for part in _SEPARATOR_RE.split(text)]
    # The split keeps the separators themselves (a capturing group) so they
    # can be dropped here in one pass rather than with a second regex.
    plan.segments = [
        seg for seg in segments
        if seg and not _SEPARATOR_RE.fullmatch(seg)
    ]
    seen: set[str] = set()
    for segment in plan.segments:
        if _is_dangerous(segment) and segment not in seen:
            seen.add(segment)
            plan.dangerous.append(segment)
    return plan


def is_dangerous_command(command: str) -> bool:
    """The `always_reprompt` predicate ToolRegistry calls per invocation."""
    return analyze(command).is_dangerous
