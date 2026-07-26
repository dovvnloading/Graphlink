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

import requests


class GitHubRestClient:
    def __init__(self, settings_manager=None):
        self.settings_manager = settings_manager

    def get_token(self):
        if self.settings_manager:
            return self.settings_manager.get_github_token().strip()
        return ""

    def build_headers(self):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self.get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def request(self, url, params=None, *, expect_json=True, timeout=25):
        response = requests.get(url, headers=self.build_headers(), params=params or {}, timeout=timeout)
        if response.status_code >= 400:
            try:
                payload = response.json()
                message = payload.get("message") or response.reason
            except ValueError:
                message = response.text or response.reason

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
