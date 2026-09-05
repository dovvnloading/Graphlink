"""PR metadata + unified-diff fetching for Review Lens.

One function, `fetch_pr_review_bundle`, turns (owner, repo, number) into
everything the review engine and the node need: PR title/state/refs, the
per-file change list, and the unified diff text itself.

Token handling is deliberately NOT reimplemented here: the caller passes an
already-constructed `GitHubRestClient` (graphlink_plugins/common/
github_client.py), which owns the token allowlist and the
status-to-user-facing-error mapping. The only header this module sets
itself is the diff media type - the one thing GitHubRestClient.request's
fixed `Accept: application/vnd.github+json` cannot express - layered on
top of that client's own auth headers, so the token allowlist still
applies unchanged.

Size discipline mirrors gitlink's context caps (graphlink_plugins/gitlink/
agent.py's MAX_FILE_CONTEXT_CHARS): the unified diff is capped at
MAX_DIFF_CHARS and each per-file patch at MAX_FILE_PATCH_CHARS, with
explicit truncated flags - the node stores the capped text and says so,
rather than silently reviewing half a diff.
"""

from __future__ import annotations

from typing import Any

import requests

MAX_PR_FILES = 100
MAX_DIFF_CHARS = 60000
MAX_FILE_PATCH_CHARS = 6000
_DIFF_TIMEOUT_SECONDS = 60

# Hard ceiling on how many BYTES of diff body are ever pulled into memory,
# independent of MAX_DIFF_CHARS (which bounds what is reviewed, after the
# fact). Four bytes per permitted character, so no realistic encoding can
# make this the binding limit for a diff that would otherwise fit.
_MAX_DIFF_DOWNLOAD_BYTES = MAX_DIFF_CHARS * 4

_KNOWN_FILE_STATUSES = frozenset({"added", "removed", "modified", "renamed", "copied", "changed", "unchanged"})


def _decode_text_bytes(raw_bytes: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[: max_chars - 3].rstrip() + "...", True


def _normalize_file_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Reduce one GET pulls/{n}/files row to the node's file shape."""
    if not isinstance(entry, dict):
        return {}
    path = str(entry.get("filename") or "").strip().replace("\\", "/")
    if not path:
        return {}
    status = str(entry.get("status") or "modified").strip().lower()
    if status not in _KNOWN_FILE_STATUSES:
        status = "modified"
    # OverflowError joins the tuple everywhere a number comes off the
    # GitHub JSON: `1e999` is valid JSON, parses to float('inf'), and
    # int(inf) raises OverflowError, which is NOT a ValueError. It escaped
    # every one of these guards and surfaced as a raw traceback in the
    # node's error banner instead of a display-safe message.
    try:
        additions = max(0, int(entry.get("additions", 0)))
    except (TypeError, ValueError, OverflowError):
        additions = 0
    try:
        deletions = max(0, int(entry.get("deletions", 0)))
    except (TypeError, ValueError, OverflowError):
        deletions = 0
    patch, patch_truncated = _truncate(str(entry.get("patch") or ""), MAX_FILE_PATCH_CHARS)
    normalized: dict[str, Any] = {
        "path": path,
        "status": status,
        "additions": additions,
        "deletions": deletions,
        "patch": patch,
        "patch_truncated": patch_truncated,
    }
    previous = str(entry.get("previous_filename") or "").strip().replace("\\", "/")
    if previous and previous != path:
        normalized["previous_path"] = previous
    return normalized


def _fetch_unified_diff(client, metadata_url: str) -> tuple[str, bool]:
    """GET the PR as a unified diff via the diff media type.

    `client.build_headers(url)` supplies the auth headers (so the token
    host-allowlist still governs this call); only Accept is overridden.
    Raises RuntimeError with a display-safe message on any failure.
    """
    headers = dict(client.build_headers(metadata_url))
    headers["Accept"] = "application/vnd.github.diff"
    try:
        # allow_redirects=False, deliberately. build_headers attaches the
        # saved token only for api.github.com / raw.githubusercontent.com
        # (see _ALLOWED_TOKEN_HOSTS), but that check runs against the URL
        # WE name - requests then follows any redirect the response asks
        # for, and its rebuild_auth only strips Authorization on a change
        # of host, not on the first hop to an attacker-named one. The real
        # API does not redirect this endpoint, so refusing to follow costs
        # nothing and keeps the allowlist decision final.
        #
        # stream=True so the body is not buffered before it can be
        # measured: the response is a diff of unknown size and
        # MAX_DIFF_CHARS was applied only after the whole thing was already
        # in memory.
        response = requests.get(
            metadata_url,
            headers=headers,
            timeout=_DIFF_TIMEOUT_SECONDS,
            allow_redirects=False,
            stream=True,
        )
    except Exception as exc:
        raise RuntimeError(f"Could not download the pull-request diff: {exc}") from exc
    if 300 <= response.status_code < 400:
        raise RuntimeError("GitHub redirected the diff download unexpectedly. Check the pull-request URL.")
    if response.status_code == 404:
        raise RuntimeError("GitHub resource not found. Check the repository and pull-request number.")
    if response.status_code == 401:
        raise RuntimeError("GitHub rejected the saved token. Update it in Settings > Integrations.")
    if response.status_code == 403:
        raise RuntimeError("GitHub refused the diff download (rate limit or permissions). Add a token or try again later.")
    if response.status_code >= 400:
        raise RuntimeError(f"GitHub refused the diff download (HTTP {response.status_code}).")
    return _truncate(_decode_text_bytes(_read_capped(response)), MAX_DIFF_CHARS)


def _read_capped(response) -> bytes:
    """At most _MAX_DIFF_DOWNLOAD_BYTES of the body, read incrementally.

    `response.content` buffers the ENTIRE body first and only then hands it
    to a truncator - so the 60,000-character cap bounded what was reviewed,
    never what was allocated. A pull request with a large generated file in
    it (or a hostile response claiming to be one) could put hundreds of
    megabytes in this process before a single character was discarded.

    The ceiling is bytes, not characters, and generous relative to
    MAX_DIFF_CHARS: multi-byte text and the truncation marker both need
    headroom, and the point is to stop unbounded growth, not to make the
    byte cap the operative limit."""
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if total >= _MAX_DIFF_DOWNLOAD_BYTES:
            break
    return b"".join(chunks)[:_MAX_DIFF_DOWNLOAD_BYTES]


def fetch_pr_review_bundle(client, owner: str, repo: str, number: int) -> dict[str, Any]:
    """Fetch everything a review of one PR needs. Raises RuntimeError (via
    the shared client or the diff downloader above) with a message safe to
    show on the node - never a traceback, never a token."""
    slug = f"{owner}/{repo}"
    metadata_url = f"https://api.github.com/repos/{slug}/pulls/{number}"
    metadata = client.request(metadata_url)
    if not isinstance(metadata, dict):
        raise RuntimeError("GitHub returned an unexpected pull-request response.")

    def _int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError, OverflowError):
            return 0

    declared_changed_files = _int(metadata.get("changed_files"))

    # The loop header is `while True`, deliberately. It used to be
    # `while len(files) < MAX_PR_FILES`, which pre-empted the cap check inside
    # the row loop below: a page that filled the list EXACTLY to the cap ended
    # the loop with hit_cap still False, so a 150-file PR reported
    # files_truncated=False while carrying only its first 100 files. Since
    # _build_overview_markdown gates its "File list truncated" line on that
    # flag, the review then claimed to cover a change it had only half read.
    #
    # Letting the loop ask for the next page instead costs one extra request
    # for a PR of exactly 100 files (that page comes back empty) and is the
    # only way to tell "exactly 100" from "the first 100 of more".
    files: list[dict[str, Any]] = []
    hit_cap = False
    page = 1
    # MAX_PR_FILES rows at 100 per page needs at most MAX_PR_FILES // 100
    # pages, plus the one extra empty page that distinguishes "exactly the
    # cap" from "the first cap of more" (see the comment above). The bound
    # exists because `while True` trusted the server to eventually return a
    # short page: a listing that keeps answering with 100 rows this loop
    # then rejects as unusable (every row missing "filename", say) never
    # advances `len(files)`, never trips the cap, and never terminates.
    max_pages = max(1, MAX_PR_FILES // 100) + 1
    while page <= max_pages:
        rows = client.request(metadata_url + "/files", params={"per_page": 100, "page": page})
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if len(files) >= MAX_PR_FILES:
                hit_cap = True  # rows left unread on the page we stopped on
                break
            normalized = _normalize_file_entry(row)
            if normalized:
                files.append(normalized)
        if hit_cap or len(rows) < 100:
            break
        page += 1

    # Two independent signals, because either alone has a blind spot: hit_cap
    # catches the listing being cut short, and the declared count catches
    # everything we dropped for any other reason (an unusable row that
    # _normalize_file_entry rejected, a listing that disagrees with the PR's
    # own metadata). Both point the same way - fewer files in hand than the PR
    # actually changed - and the flag exists to say exactly that.
    files_truncated = hit_cap or declared_changed_files > len(files)

    diff_text, diff_truncated = _fetch_unified_diff(client, metadata_url)

    # Bound to locals before the isinstance check rather than looked up twice
    # inline: a check on one call cannot tell a type checker anything about a
    # second, so the narrowing has to happen on a single value.
    raw_base = metadata.get("base")
    raw_head = metadata.get("head")
    base = raw_base if isinstance(raw_base, dict) else {}
    head = raw_head if isinstance(raw_head, dict) else {}
    return {
        "repo": slug,
        "pr_number": number,
        "pr_title": str(metadata.get("title") or "").strip(),
        "pr_state": str(metadata.get("state") or "").strip().lower(),
        "html_url": str(metadata.get("html_url") or "").strip(),
        "base_ref": str(base.get("ref") or "").strip(),
        "head_ref": str(head.get("ref") or "").strip(),
        "additions": _int(metadata.get("additions")),
        "deletions": _int(metadata.get("deletions")),
        "changed_files": declared_changed_files or len(files),
        "files": files,
        "files_truncated": files_truncated,
        "diff_text": diff_text,
        "diff_truncated": diff_truncated,
        "diff_chars": len(diff_text),
    }
