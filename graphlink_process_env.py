"""ADR-002 P0: an explicit-allowlist environment builder for subprocesses
that execute AI-generated code - originally written to close a real gap in
two callers that had called subprocess.Popen(...) with no `env=` argument
at all (PythonREPL, now graphlink_plugins/common/python_repl.py; the
Virtual Environment Runner's venv-create/pip-install/script-run trio,
graphlink_plugins/code_sandbox/domain.py). Omitting `env=` means Python's
default applies: the child inherits the FULL parent os.environ - including
every provider API key this app itself reads via os.environ.get(...)
fallbacks (see api_provider.py), if the user has configured one as an
environment variable rather than through Settings. Every subsequent
subprocess-spawning tool in this codebase (e.g. the harness's shell.exec,
backend/harness/tools_shell.py) is built on this module from the start.

An ALLOWLIST, not a blocklist: block-listing known secret var names is
fragile - a user's own OS environment can carry secrets under names this
codebase has never heard of (a personal AWS_SECRET_ACCESS_KEY, a work VPN
token, anything). Only the variables a plain `python`/`pip`/`venv`
subprocess actually needs to function are copied through; every provider
key this app itself reads from the environment is excluded by
construction, not by name-matching.

Deliberately conservative: proxy variables (HTTP_PROXY/HTTPS_PROXY/
NO_PROXY) are NOT included, even though excluding them could break `pip
install` behind a corporate proxy - a proxy URL can itself carry embedded
credentials (`http://user:pass@host:port`), and "secure by default" wins
over that convenience for code the user has not yet reviewed.
"""

import os

# The minimum a `python`/`pip`/`venv` subprocess needs to run at all on
# Windows: resolve the interpreter/its DLLs, create a venv, resolve DNS for
# `pip install`, and read/write temp files. A short list of POSIX
# equivalents is included too, for parity if this ever runs on macOS/Linux -
# none of these carry secrets.
_SAFE_ENV_VAR_NAMES = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER",
        "OS",
        # POSIX equivalents.
        "HOME",
        "LANG",
        "LC_ALL",
        "SHELL",
        "USER",
    }
)


def safe_subprocess_env() -> dict[str, str]:
    """Build an explicit-allowlist environment dict for a subprocess that
    will execute AI-generated code. Pass this as
    `subprocess.Popen(..., env=safe_subprocess_env())` - never omit `env=`
    at one of these call sites, which would silently fall back to
    inheriting the full parent environment.

    Case-insensitive on the allowlist match (Windows env var casing in
    os.environ is inconsistent - e.g. "SystemRoot" vs "SYSTEMROOT" -
    while POSIX systems are case-sensitive by convention; comparing
    upper-cased names is correct for both)."""
    return {name: value for name, value in os.environ.items() if name.upper() in _SAFE_ENV_VAR_NAMES}
