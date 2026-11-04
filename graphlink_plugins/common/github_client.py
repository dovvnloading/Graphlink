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
# GitHub's authenticated zipball API normally redirects to codeload. That
# redirect must be allowed for downloading, but NEVER receive the saved token.
_ALLOWED_DOWNLOAD_HOSTS = _ALLOWED_TOKEN_HOSTS | {"codeload.github.com", "objects.githubusercontent.com"}
_MAX_DOWNLOAD_REDIRECTS = 3


def _is_safe_github_url(url: str, hosts: frozenset[str] | set[str]) -> bool:
    try:
        parsed = urlparse(url)
        return (
            parsed.scheme == "https" and parsed.hostname in hosts
            and parsed.port in (None, 443)
            and not parsed.username and not parsed.password
        )
    except (TypeError, ValueError):
        return False


def is_trusted_github_url(url: str) -> bool:
    """Only authenticated HTTPS URLs on known GitHub hosts are trusted.

    A provider-returned download_url can be an attacker-controlled string.
    A hostname-only allowlist mistakenly authorized cleartext HTTP requests
    and unusual ports on an otherwise matching hostname.
    """
    return _is_safe_github_url(url, _ALLOWED_TOKEN_HOSTS)


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
        if url is not None and not is_trusted_github_url(url):
            return headers
        token = self.get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _download_bounded(self, url, params, timeout, max_bytes):
        """Stream GitHub binaries with limits and explicit safe redirects.

        requests.get(...).content eagerly allocates the ENTIRE response.
        Gitlink uses this path for repository ZIPs and raw-source fallbacks:
        an oversized or highly compressed remote response must not exhaust
        local RAM before our ZIP import size checks get a chance to run.
        """
        if not is_trusted_github_url(url):
            raise RuntimeError("GitHub download URL is not a trusted HTTPS address.")
        if not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        current_url = url
        for hop in range(_MAX_DOWNLOAD_REDIRECTS + 1):
            response = requests.get(
                current_url,
                headers=self.build_headers(current_url),
                params=params if hop == 0 else {},
                timeout=timeout,
                stream=True,
                allow_redirects=False,
            )
            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if hop >= _MAX_DOWNLOAD_REDIRECTS or not location:
                        raise RuntimeError("GitHub download exceeded its redirect limit.")
                    redirected = urljoin(current_url, location)
                    if not _is_safe_github_url(redirected, _ALLOWED_DOWNLOAD_HOSTS):
                        raise RuntimeError("GitHub download redirected to an unsafe URL.")
                    current_url = redirected
                    continue
                if response.status_code >= 400:
                    if response.status_code == 404:
                        raise RuntimeError("GitHub resource not found. Check the repository, branch, and file path.")
                    if response.status_code == 401:
                        raise RuntimeError("GitHub rejected the saved token. Update it in Settings > Integrations.")
                    raise RuntimeError(f"GitHub download returned HTTP {response.status_code}.")
                if response.status_code < 200 or response.status_code >= 300:
                    raise RuntimeError(f"GitHub download returned HTTP {response.status_code}.")
                content_length = response.headers.get("Content-Length", "")
                if content_length.isdigit() and int(content_length) > max_bytes:
                    raise RuntimeError("GitHub download exceeds the configured size limit.")
                body = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    if len(body) + len(chunk) > max_bytes:
                        raise RuntimeError("GitHub download exceeds the configured size limit.")
                    body.extend(chunk)
                return bytes(body)
            finally:
                response.close()
        raise RuntimeError("GitHub download exceeded its redirect limit.")

    def request(self, url, params=None, *, expect_json=True, timeout=25, max_bytes=None):
        if max_bytes is not None:
            if expect_json:
                raise ValueError("bounded downloads are intended for binary content")
            return self._download_bounded(url, params or {}, timeout, max_bytes)
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
