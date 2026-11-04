"""Shared GitHub REST client for plugins that read repository data.

Extracted from near-identical code independently hand-rolled in
graphlink_plugin_code_review.py and graphlink_plugin_gitlink.py: token retrieval, header
construction, and HTTP-status-to-user-facing-error mapping were copy-pasted between
the two files, so a fix (e.g. to rate-limit handling) in one would not reach the other.

This only covers that low-level boilerplate. Each plugin's higher-level repo/tree/
file-loading methods (load_github_repositories, _resolve_repo_and_branch, etc.) have
real per-plugin UI side effects and are not part of this extraction - they call into
this client instead of duplicating its logic.
"""

import time
from urllib.parse import urljoin, urlparse

import requests

# SECURITY-FIX: fetch_github_file_text (graphlink_plugins/gitlink/repository.py)
# passes a `download_url` taken verbatim from a GitHub contents-API response
# straight into request() - unlike every other call site in this codebase,
# which builds its URL from a fixed "https://api.github.com/..." literal, this
# one trusts the RESPONSE to say where to go next. requests only strips
# Authorization on a cross-host redirect (rebuild_auth), never on a direct
# request to a URL an attacker-controlled/tampered response supplied
# outright, so a hostile response (TLS interception, a compromised proxy, a
# local DNS/hosts redirect of api.github.com - all real, if narrow, threats)
# could redirect the saved token to any host it names. Only these two real
# GitHub hosts ever legitimately need the token attached.
_ALLOWED_TOKEN_HOSTS = frozenset({"api.github.com", "raw.githubusercontent.com"})
_BOUNDED_GITHUB_HOSTS = frozenset({"api.github.com", "codeload.github.com", "raw.githubusercontent.com"})
_MAX_ARCHIVE_REDIRECTS = 3


def _download_clock() -> float:
    """Isolated clock seam for deadline tests; never replace global time."""
    return time.monotonic()


class GitHubRestClient:
    def __init__(self, settings_manager=None):
        self.settings_manager = settings_manager

    def get_token(self):
        if self.settings_manager:
            return self.settings_manager.get_github_token().strip()
        return ""

    def build_headers(self, url=None):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        # SECURITY-FIX: see _ALLOWED_TOKEN_HOSTS' own comment above. `url`
        # is optional (defaults to the pre-fix "always attach" behavior) so
        # a caller that already knows it's only ever talking to a trusted
        # host - or a caller with no URL at all yet - is unaffected;
        # request() below always passes the real target URL.
        if url is not None and urlparse(url).hostname not in _ALLOWED_TOKEN_HOSTS:
            return headers
        token = self.get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def request(self, url, params=None, *, expect_json=True, timeout=25, max_bytes=None):
        if max_bytes is not None:
            return self._download_bounded(url, params, timeout, expect_json, max_bytes)
        response = requests.get(url, headers=self.build_headers(url), params=params or {}, timeout=timeout)
        if response.status_code >= 400:
            # The error body is only usable if it is a JSON OBJECT. A valid
            # JSON list or bare string (a proxy or error page can return
            # either) made `payload.get` raise AttributeError - an uncaught
            # non-RuntimeError escaping a method every caller wraps expecting
            # a display-safe RuntimeError. The `response.text` fallback is
            # also length-bounded now: it is upstream-controlled and went
            # verbatim into a node's error banner.
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                message = payload.get("message") or response.reason
            else:
                message = (response.text or "")[:500] or response.reason
            message = str(message or "")

            if response.status_code == 404:
                raise RuntimeError("GitHub resource not found. Check the repository, branch, and file path.")
            if response.status_code == 401:
                raise RuntimeError("GitHub rejected the saved token. Update it in Settings > Integrations.")
            if response.status_code == 403 and "rate limit" in message.lower():
                raise RuntimeError("GitHub API rate limit reached. Add a token or try again later.")
            raise RuntimeError(message)

        if not expect_json:
            return response.content
        return response.json()

    def _download_bounded(self, url, params, timeout, expect_json, max_bytes):
        """Bound GitHub binary downloads and restrict each redirect hop."""
        if expect_json or type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("bounded downloads require a positive byte cap and expect_json=False")
        current_url = url
        started = _download_clock()
        # One read timeout resets for every socket.recv and is not a total
        # budget: a server dribbling bytes forever can evade it. Bound the
        # whole chain, including redirect hops, to an explicit deadline.
        total_budget = min(120.0, max(10.0, float(timeout) * 1.5))
        for hop in range(_MAX_ARCHIVE_REDIRECTS + 1):
            if _download_clock() - started > total_budget:
                raise RuntimeError("GitHub download exceeded the total time limit.")
            try:
                parsed = urlparse(current_url)
                allowed = (
                    parsed.scheme == "https" and parsed.hostname in _BOUNDED_GITHUB_HOSTS
                    and parsed.username is None and parsed.password is None
                )
            except ValueError:
                allowed = False
            if not allowed:
                raise RuntimeError("GitHub archive download was redirected to an untrusted destination.")
            # Never forward API credentials to the distinct codeload host.
            response = requests.get(
                current_url, headers=self.build_headers(current_url),
                params=params or {} if hop == 0 else {},
                timeout=timeout, stream=True, allow_redirects=False,
            )
            try:
                if 300 <= response.status_code < 400:
                    location = response.headers.get("Location")
                    if not location:
                        raise RuntimeError("GitHub archive download redirected without a destination.")
                    current_url = urljoin(current_url, location)
                    continue
                if response.status_code >= 400:
                    if response.status_code == 401:
                        raise RuntimeError("GitHub rejected the saved token. Update it in Settings > Integrations.")
                    if response.status_code == 404:
                        raise RuntimeError("GitHub repository or branch was not found.")
                    raise RuntimeError(f"GitHub archive download failed (HTTP {response.status_code}).")
                length = response.headers.get("Content-Length", "")
                if length.isdecimal() and int(length) > max_bytes:
                    raise RuntimeError("GitHub archive download exceeds the compressed size limit.")
                data = bytearray()
                # urllib3's buffered read(amt) may wait for *amt* bytes
                # while restarting its per-recv timeout on every byte.
                # Read one byte at a time so the total deadline is checked
                # even against a slow-drip server, with the underlying
                # socket still buffered for normal fast downloads.
                for chunk in response.iter_content(chunk_size=1):
                    if _download_clock() - started > total_budget:
                        raise RuntimeError("GitHub download exceeded the total time limit.")
                    if not chunk:
                        continue
                    if len(data) + len(chunk) > max_bytes:
                        raise RuntimeError("GitHub archive download exceeds the compressed size limit.")
                    data.extend(chunk)
                if _download_clock() - started > total_budget:
                    raise RuntimeError("GitHub download exceeded the total time limit.")
                return bytes(data)
            finally:
                response.close()
        raise RuntimeError("GitHub archive download exceeded the redirect limit.")
