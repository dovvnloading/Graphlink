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

from urllib.parse import urlparse

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

    def request(self, url, params=None, *, expect_json=True, timeout=25):
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
