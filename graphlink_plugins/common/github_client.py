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
# The GitHub zipball API redirects to codeload. No other fetch hosts
# are necessary, even if an untrusted response supplies a download URL.
_ALLOWED_FETCH_HOSTS = _ALLOWED_TOKEN_HOSTS | {"codeload.github.com"}
_MAX_REDIRECTS = 4
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024


def _trusted_github_url(url):
    """Validate every requested/redirected destination before sending GET."""
    if not isinstance(url, str):
        raise RuntimeError("Refusing an untrusted GitHub download URL.")
    try:
        parsed = urlparse(url)
        valid_port = parsed.port in (None, 443)
    except ValueError as exc:
        raise RuntimeError("Refusing a malformed GitHub download URL.") from exc
    if (parsed.scheme != "https" or parsed.hostname not in _ALLOWED_FETCH_HOSTS
            or not valid_port or parsed.username is not None
            or parsed.password is not None or parsed.fragment):
        raise RuntimeError("Refusing an untrusted GitHub download URL (HTTPS GitHub hosts only).")
    return url


def _read_capped_download(response):
    """Bound streamed binary results even if the server omits Content-Length."""
    length = response.headers.get("Content-Length", "")
    if str(length).isdigit() and int(length) > MAX_DOWNLOAD_BYTES:
        raise RuntimeError("GitHub download exceeds the maximum permitted size.")
    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_DOWNLOAD_BYTES:
            raise RuntimeError("GitHub download exceeds the maximum permitted size.")
        chunks.append(chunk)
    return b"".join(chunks)


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
        if url is not None:
            try:
                parsed = urlparse(url)
                trusted = (parsed.scheme == "https"
                           and parsed.hostname in _ALLOWED_TOKEN_HOSTS
                           and parsed.port in (None, 443)
                           and parsed.username is None and parsed.password is None)
            except (ValueError, TypeError):
                trusted = False
            if not trusted:
                return headers
        token = self.get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def request(self, url, params=None, *, expect_json=True, timeout=25):
        current_url = _trusted_github_url(url)
        for redirect_count in range(_MAX_REDIRECTS + 1):
            # requests follows redirects automatically unless disabled.
            # A 302 to localhost must be rejected BEFORE any GET is sent.
            response = requests.get(
                current_url,
                headers=self.build_headers(current_url),
                params=(params or {}) if redirect_count == 0 else {},
                timeout=timeout,
                allow_redirects=False,
                stream=not expect_json,
            )
            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    if redirect_count >= _MAX_REDIRECTS:
                        raise RuntimeError("GitHub download exceeded the redirect limit.")
                    location = response.headers.get("Location")
                    if not location:
                        raise RuntimeError("GitHub download returned a redirect without a location.")
                    current_url = _trusted_github_url(urljoin(current_url, location))
                    continue

                if response.status_code >= 400:
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = None
                    if isinstance(payload, dict):
                        message = payload.get("message") or response.reason
                    else:
                        message = (response.text or "")[:500] or response.reason
                    message = str(message or "")[:500]
                    if response.status_code == 404:
                        raise RuntimeError("GitHub resource not found. Check the repository, branch, and file path.")
                    if response.status_code == 401:
                        raise RuntimeError("GitHub rejected the saved token. Update it in Settings > Integrations.")
                    if response.status_code == 403 and "rate limit" in message.lower():
                        raise RuntimeError("GitHub API rate limit reached. Add a token or try again later.")
                    raise RuntimeError(message)

                if not expect_json:
                    return _read_capped_download(response)
                return response.json()
            finally:
                response.close()

        raise RuntimeError("GitHub download exceeded the redirect limit.")
