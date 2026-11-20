"""Per-task workspace agent permission modes.

This is a *narrow exemption* to the existing tool approval gate. It never
grants an extra scope, applies outside an Agent run, approves network access,
or converts an ambiguous command into a trusted command. The actual handlers
retain their own path confinement, cancellation and execution guards.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from backend.harness import shell_policy
from backend.harness.workspace import WorkspaceError, resolve_under_root

ASK = "ask"
AUTO_EDIT = "auto_edit"
AUTONOMOUS = "autonomous"
VALID_PERMISSION_MODES = frozenset({ASK, AUTO_EDIT, AUTONOMOUS})

_METADATA_DIRS = frozenset({".git", ".hg", ".svn"})
_NETWORK_WORDS = re.compile(r"(?i)(?:https?://|ftp://|git@|www\.|\b(?:curl|wget|ssh|scp|sftp|rsync|telnet|nc|ncat|ping)\b)")
_NETWORK_COMMANDS = frozenset({"curl", "wget", "ssh", "scp", "sftp", "rsync", "ftp",
                               "telnet", "nc", "ncat", "ping", "dig", "nslookup"})
_GIT_NETWORK = frozenset({"fetch", "pull", "push", "clone", "ls-remote", "submodule"})
_INSTALL_NETWORK = frozenset({"install", "add", "update", "upgrade", "ci", "download", "publish"})
_PYTHON_UNSAFE_MODULES = frozenset({
    "socket", "ssl", "requests", "urllib", "http", "httpx", "aiohttp",
    "ftplib", "smtplib", "imaplib", "poplib", "webbrowser", "subprocess",
    "multiprocessing", "ctypes", "importlib", "builtins", "pickle",
    "marshal", "shutil", "tempfile", "os", "sys", "pathlib2",
})
_PYTHON_UNSAFE_CALLS = frozenset({
    "eval", "exec", "compile", "__import__", "breakpoint", "getattr",
    "setattr", "delattr", "globals", "locals", "vars", "input",
    "system", "popen", "spawn", "run", "call", "check_call", "check_output",
    "Popen", "remove", "unlink", "rmdir", "rmtree", "rename", "replace",
    "chmod", "chown", "symlink_to", "hardlink_to", "link", "connect",
    "urlopen", "urlretrieve", "request", "get", "post", "put", "delete",
    "home", "cwd", "chdir", "expanduser", "resolve",
})
_PATH_CALLS = frozenset({"open", "Path", "PurePath", "PosixPath", "WindowsPath"})


def normalize_permission_mode(mode: object) -> str:
    """Unknown or hand-edited values never acquire implicit privileges."""
    return mode if isinstance(mode, str) and mode in VALID_PERMISSION_MODES else ASK


def effective_permission_mode(mode: object, profile: object) -> str:
    """Autonomous cannot be enabled by changing serialized node state alone."""
    chosen = normalize_permission_mode(mode)
    if chosen == AUTONOMOUS and profile != "isolated":
        return ASK
    return chosen


def _within(root: Path, raw: str) -> bool:
    if not raw or "\x00" in raw or raw.startswith("~") or "$" in raw or "%" in raw:
        return False
    # Shell commands run *inside* the container, where the one project mount
    # appears at /workspace rather than the host's trusted directory.
    if raw == "/workspace":
        raw = "."
    elif raw.startswith("/workspace/"):
        raw = raw[len("/workspace/"):]
    try:
        path = resolve_under_root(root, raw)
    except (WorkspaceError, OSError, ValueError, RuntimeError):
        return False
    return not any(part.casefold() in _METADATA_DIRS for part in path.parts)


def _safe_shell(command: object, root: Path, shell: str) -> bool:
    if not isinstance(command, str) or not command.strip() or len(command) > 4000:
        return False
    if _NETWORK_WORDS.search(command):
        return False
    # The existing conservative prefix parser rejects unknown shell grammar,
    # redirections, command substitutions, inline interpreter code and
    # quoted/escaped executable names. Unlike the explicit allow-list, no
    # prefix is actually remembered by this mode.
    try:
        candidates = shell_policy.shell_allow_rule_candidates(command, shell=shell)
        selected = shell_policy.effective_shell_for_rules(shell)
        plan = shell_policy.analyze(
            command, shell="bash" if selected == "git-bash" else selected,
        )
    except Exception:
        return False
    if not candidates or plan.is_dangerous or len(candidates) != len(plan.segments):
        return False
    for segment in plan.segments:
        words = segment.split()
        if not words:
            return False
        program = words[0].rsplit("/", 1)[-1].lower()
        args = [word.lower() for word in words[1:]]
        if program in _NETWORK_COMMANDS:
            return False
        if program == "git" and any(word in _GIT_NETWORK for word in args):
            return False
        if program in {"pip", "pip3", "npm", "npx", "yarn", "pnpm", "uv",
                       "poetry", "cargo", "gem", "apt", "apt-get"} and (
            any(word in _INSTALL_NETWORK for word in args) or program == "npx"
        ):
            return False
        if program in {"python", "python3", "py", "node", "ruby", "perl",
                       "bash", "sh", "zsh", "powershell", "pwsh", "cmd"} and any(
            arg in {"-c", "-e", "-r", "/c", "-command", "-encodedcommand"}
            for arg in args
        ):
            return False
        for index, word in enumerate(words):
            if word.startswith("file://") or word.startswith("\\\\"):
                return False
            # A lexical check of every path-looking token, including paths
            # embedded in --option=value, with symlink resolution on host.
            candidate = word.split("=", 1)[-1] if "=" in word else word
            if candidate.startswith(("/", "./", "../", "~", "\\")) or (
                "/" in candidate and "://" not in candidate
            ):
                if index == 0 and candidate.startswith("/") and not _within(root, candidate):
                    return False
                if not _within(root, candidate):
                    return False
            if candidate.startswith("-") and ".." in candidate:
                return False
    return True


def _safe_python(source: object, root: Path) -> bool:
    if not isinstance(source, str) or not source.strip():
        return False
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, TypeError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name.split(".", 1)[0] for alias in node.names] if isinstance(node, ast.Import) else [
                (node.module or "").split(".", 1)[0]
            ]
            if any(name in _PYTHON_UNSAFE_MODULES or name.startswith("_") for name in names):
                return False
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            return False
        if isinstance(node, (ast.Name, ast.Attribute)):
            ident = node.id if isinstance(node, ast.Name) else node.attr
            if ident.startswith("__") or ident in _PYTHON_UNSAFE_CALLS:
                return False
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if _NETWORK_WORDS.search(value):
                return False
            if value.startswith(("/", "../", "./", "~", "\\")) or (
                "/" in value and not value.startswith(("http:", "https:"))
            ):
                if not _within(root, value):
                    return False
        if isinstance(node, ast.Call):
            target = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else ""
            )
            if not target or target in _PYTHON_UNSAFE_CALLS:
                return False
            if target in _PATH_CALLS:
                if not node.args or not isinstance(node.args[0], ast.Constant):
                    return False
                path = node.args[0].value
                if not isinstance(path, str) or not _within(root, path):
                    return False
        # Dynamic code generation can hide side effects from this screening.
        if isinstance(node, (ast.Lambda, ast.Yield, ast.YieldFrom, ast.Await)):
            return False
    return True


def may_skip_workspace_approval(call, ctx) -> bool:
    """True only for clearly in-root actions of a *snapshotted* Agent task.

    In particular the caller's scope check runs before this test. This is
    never used for network or graph mutations, regardless of their scopes.
    """
    root = getattr(ctx, "harness_workspace_dir", None)
    if not isinstance(root, Path) or not getattr(ctx, "harness_node_id", None) or not getattr(ctx, "harness_workspace_id", None):
        return False
    profile = getattr(ctx, "harness_execution_profile", "host")
    mode = effective_permission_mode(getattr(ctx, "harness_permission_mode", ASK), profile)
    if mode == ASK:
        return False
    if call.name in {"fs.write", "fs.edit"}:
        raw = call.arguments.get("path")
        return isinstance(raw, str) and _within(root, raw)
    if mode != AUTONOMOUS or profile != "isolated":
        return False
    if call.name == "python.install":
        # Only offline cached-wheel installs bypass. The installer requests a
        # fresh, bound approval before *any* PyPI download on a cache miss.
        return True
    if call.name == "python.exec":
        return _safe_python(call.arguments.get("code"), root)
    if call.name == "shell.exec":
        if call.arguments.get("network") is not None and call.arguments.get("network") is not False:
            return False
        return _safe_shell(call.arguments.get("command"), root, getattr(ctx, "harness_shell", "auto"))
    if call.name == "shell.session":
        action = call.arguments.get("action")
        if action in ("read", "list", "stop"):
            return True
        if action == "start":
            return _safe_shell(call.arguments.get("command"), root, getattr(ctx, "harness_shell", "auto"))
        # A session's stdin may target a Python/Node/shell REPL; its language
        # cannot be inferred from the input alone. Ask for every write.
    return False
