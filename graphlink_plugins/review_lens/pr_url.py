"""PR URL parsing for Review Lens.

Accepts the copy-pasted GitHub PR URLs a reviewer actually has on hand -
with or without trailing slash, /files//commits suffix, query string, or
fragment - and reduces them to (owner, repo, number). Anything else raises
RuntimeError with a message safe to show on the node (no tokens, no
tracebacks, just what shape was expected).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

_PR_PATH_PATTERN = re.compile(r"^/([^/]+)/([^/]+)/pull/(\d+)(?:/(?:files|commits|checks))?/?$")


def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
    """Parse a GitHub PR URL into (owner, repo, pull_number).

    Raises RuntimeError for anything that is not recognizably a
    `github.com/{owner}/{repo}/pull/{number}` URL.
    """
    text = (pr_url or "").strip()
    if not text:
        raise RuntimeError("Paste a GitHub pull-request URL first, e.g. https://github.com/owner/repo/pull/123.")
    # Tolerate a missing scheme the way a pasted address-bar value sometimes
    # arrives ("github.com/owner/repo/pull/123").
    candidate = text if "://" in text else f"https://{text}"
    try:
        parsed = urlparse(candidate)
    except ValueError:
        raise RuntimeError("That URL could not be read as a GitHub pull-request link.") from None
    if parsed.hostname not in {"github.com", "www.github.com"}:
        raise RuntimeError("Only github.com pull-request URLs are supported.")
    match = _PR_PATH_PATTERN.match(parsed.path or "")
    if not match:
        raise RuntimeError(
            "That URL is not a pull-request link - expected https://github.com/{owner}/{repo}/pull/{number}."
        )
    owner, repo, number_text = match.group(1), match.group(2), match.group(3)
    # A ".git"-suffixed repo segment ("repo.git/pull/123") is never a real PR
    # path - strip it rather than querying a repo literally named "repo.git".
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    try:
        number = int(number_text)
    except ValueError:  # pragma: no cover - the regex above only matches digits
        raise RuntimeError("That URL is not a pull-request link.") from None
    if number <= 0:
        raise RuntimeError("That URL is not a pull-request link.")
    return owner, repo, number


def canonical_pr_slug(owner: str, repo: str, number: int) -> str:
    """The short human label shown on the node, e.g. "owner/repo#123"."""
    return f"{owner}/{repo}#{number}"
