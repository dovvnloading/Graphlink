"""ADR-009 stage 9.3: the one secret-scrub chokepoint.

Every surface that lets data LEAVE this machine goes through `scrub()`:
the `.graphlink` export (stage 9.4), and - once they exist - ADR-014/008
recipe templates and any future share surface. One function, one test
file, one place to audit.

WHY VALUE-BASED, NOT JUST KEY-BASED. A scrubber that only redacts known
field NAMES (`openai_api_key`, ...) is one refactor away from useless: the
day a secret gets copied into a differently-named field, or interpolated
into an error string stored on a node, or pasted by the user into their own
chat text, a name-only filter waves it straight through. So this scrubs on
BOTH axes - the name of the field it sits in, AND the shape of the value
itself. A value that looks like a credential is redacted no matter what
key it arrived under, including inside free text.

WHY PATHS TOO. An absolute path is not a credential but it is personal:
`C:\\Users\\<real name>\\...` carries the operator's account name, and
deeper segments carry private folder and file names. Paths are redacted
whole rather than trimmed to a basename, because the basename is exactly
where the private part usually lives (`quarterly_layoffs.xlsx`).

DELIBERATELY OVER-BROAD. This function will sometimes redact a string that
was not actually a secret - a base64 blob, a long opaque id, a path the
user pasted deliberately. That direction is correct: a redacted non-secret
costs a slightly less useful export, while a leaked real secret costs the
user their account. Every rule here is chosen to fail toward redaction.

NOT ENCRYPTION, NOT AUTHORIZATION. This removes secrets from data already
destined to leave. It does not decide WHETHER something may leave; callers
own that.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote_plus

REDACTED = "[redacted]"
REDACTED_PATH = "[redacted-path]"

# Field names whose VALUE is always a secret, matched case-insensitively.
# Exact names come from graphlink_settings_store.SettingsManager.SECRET_KEYS
# (kept in sync by test_secret_scrub.py, which imports that tuple and
# asserts every member is covered here) - the suffix rules below then
# generalize to fields that do not exist yet.
_SECRET_KEY_SUFFIXES = (
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "auth_token",
    "token",
    "secret",
    "password",
    "passphrase",
    "credential",
    "credentials",
    "private_key",
)

# Value shapes that are a credential regardless of the field they sit in.
# Each is anchored to a real issuer format rather than "long random-looking
# string", so ordinary content is not shredded wholesale.
_SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),           # OpenAI-style
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),       # Anthropic
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),          # Google API key
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),       # GitHub token family
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),     # GitHub fine-grained PAT
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),    # Slack
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"),  # generic bearer header
    # This codebase's own at-rest wrapper (graphlink_secrets.py). The
    # ciphertext is only decryptable by this Windows account, but it is
    # still the secret's stored form and has no business in an export.
    re.compile(r"dpapi:[A-Za-z0-9+/=]+"),
)

# Absolute filesystem paths: Windows drive-letter, UNC, and POSIX home-ish
# roots. Deliberately NOT a bare `/...` match - that would eat ordinary
# prose containing slashes, and URLs, for no gain.
_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|]*"),
    re.compile(r"\\\\[^\s\"'<>|]+"),
    re.compile(r"/(?:home|Users|root|var|tmp|opt|mnt|media)/[^\s\"'<>|]*"),
)


# A short or percent-encoded credential in a URL query may not match any
# issuer-specific value pattern above. Work from the PARAMETER NAME, not the
# value's shape; preserve the original URL's casing, escaping and unrelated
# parameters. This also catches query fragments embedded in free text or
# relative URLs. The optional "amp;" handles HTML-escaped "&amp;token=..."
# without having to unescape/re-encode the original string.
_QUERY_PARAM_RE = re.compile(
    r"""(?P<prefix>[?&](?:amp;)?)(?P<key>[^?&=#\s<>"'\x60]+)=(?P<value>[^&#\s<>"'\x60]*)""",
    re.IGNORECASE,
)
# URL punctuation in prose/Markdown is not itself part of the credential.
_QUERY_TRAILING_PUNCTUATION = ".,;!?)]}"


def _key_is_secret(key: str) -> bool:
    lowered = key.lower()
    return any(lowered.endswith(suffix) for suffix in _SECRET_KEY_SUFFIXES)


def _scrub_query_parameter(match: re.Match[str]) -> str:
    key = match.group("key")
    # Names may use case variants, hyphens or percent-encoding, e.g.
    # API%5FKEY / api-key. Decode ONLY for the decision; rewrite only the
    # value so a benign query parameter never gets re-serialized.
    normalized_key = unquote_plus(key).replace("-", "_").replace(".", "_")
    value = match.group("value")
    if not value or not _key_is_secret(normalized_key):
        return match.group(0)

    # Keep scrub_text idempotent, including Markdown URL punctuation after
    # the marker (e.g. ?token=[redacted]).).
    if value.startswith(REDACTED) and all(
        ch in _QUERY_TRAILING_PUNCTUATION for ch in value[len(REDACTED):]
    ):
        return match.group(0)

    without_punctuation = value.rstrip(_QUERY_TRAILING_PUNCTUATION)
    trailing = value[len(without_punctuation):] if without_punctuation else ""
    return f"{match.group('prefix')}{key}={REDACTED}{trailing}"


def scrub_text(text: str) -> str:
    """Redacts credentials (including sensitive URL query parameters) and
    private paths inside one string, leaving surrounding text intact.
    Applied to every string
    reached by scrub(), not just to values under suspicious keys - an error
    message stored on a node ("failed to read C:\\Users\\ada\\taxes.csv")
    is exactly the kind of incidental leak a key-name filter misses."""
    result = _QUERY_PARAM_RE.sub(_scrub_query_parameter, text)
    for pattern in _SECRET_VALUE_PATTERNS:
        result = pattern.sub(REDACTED, result)
    for pattern in _PATH_PATTERNS:
        result = pattern.sub(REDACTED_PATH, result)
    return result


def scrub(value: Any) -> Any:
    """Returns a scrubbed deep copy of `value`. Never mutates its input -
    callers routinely pass live in-memory state (a SceneDocument's own
    payload) that must not be damaged by the act of exporting it.

    Containers recurse; strings go through scrub_text; a value under a
    secret-named key is replaced wholesale rather than pattern-matched,
    since a credential in a field literally called `api_key` should not
    have to match a known issuer format to be caught. Non-string scalars
    (int/float/bool/None) pass through untouched - they cannot carry a
    credential, and mangling them would corrupt the export's structure."""
    if isinstance(value, dict):
        scrubbed: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _key_is_secret(key):
                # Preserve "was set" vs "was empty" - useful in a bug
                # report, and it leaks nothing.
                scrubbed[key] = REDACTED if item else item
            else:
                scrubbed[key] = scrub(item)
        return scrubbed
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub(item) for item in value)
    if isinstance(value, str):
        return scrub_text(value)
    return value
