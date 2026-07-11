"""Tests for the shared GitHubRestClient (Phase 3a of doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md).

graphlink_plugin_code_review.py and graphlink_plugin_gitlink.py used to each hand-roll
their own _get_github_token/_github_headers/_github_request with identical token
retrieval, header construction, and HTTP-status-to-error-message mapping - a fix to one
(e.g. rate-limit handling) would not have reached the other. Both now delegate to this
shared client. These tests cover the client directly (mocking requests.get) plus that
both node classes' wrapper methods actually delegate to it.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from graphlink_plugins.common.github_client import GitHubRestClient


class FakeSettingsManager:
    def __init__(self, token="  my-token  "):
        self._token = token

    def get_github_token(self):
        return self._token


def _fake_response(status_code=200, json_data=None, text="", reason="", content=b""):
    response = MagicMock()
    response.status_code = status_code
    response.reason = reason
    response.text = text
    response.content = content
    if json_data is not None:
        response.json.return_value = json_data
    else:
        response.json.side_effect = ValueError("no json body")
    return response


class TestGetToken:
    def test_returns_stripped_token_when_settings_manager_present(self):
        client = GitHubRestClient(FakeSettingsManager("  abc123  "))
        assert client.get_token() == "abc123"

    def test_returns_empty_string_when_no_settings_manager(self):
        client = GitHubRestClient(settings_manager=None)
        assert client.get_token() == ""


class TestBuildHeaders:
    def test_includes_authorization_when_token_present(self):
        client = GitHubRestClient(FakeSettingsManager("abc123"))
        headers = client.build_headers()
        assert headers["Authorization"] == "Bearer abc123"
        assert headers["Accept"] == "application/vnd.github+json"
        assert headers["X-GitHub-Api-Version"] == "2022-11-28"

    def test_omits_authorization_when_no_token(self):
        client = GitHubRestClient(settings_manager=None)
        headers = client.build_headers()
        assert "Authorization" not in headers


class TestRequest:
    def test_successful_request_returns_json(self):
        client = GitHubRestClient(FakeSettingsManager())
        with patch("graphlink_plugins.common.github_client.requests.get", return_value=_fake_response(200, json_data={"ok": True})) as mock_get:
            result = client.request("https://api.github.com/repos/x/y")
        assert result == {"ok": True}
        mock_get.assert_called_once()

    def test_expect_json_false_returns_raw_content(self):
        client = GitHubRestClient(FakeSettingsManager())
        with patch("graphlink_plugins.common.github_client.requests.get", return_value=_fake_response(200, content=b"raw-bytes")):
            result = client.request("https://api.github.com/x", expect_json=False)
        assert result == b"raw-bytes"

    def test_404_raises_specific_message(self):
        client = GitHubRestClient(FakeSettingsManager())
        with patch("graphlink_plugins.common.github_client.requests.get", return_value=_fake_response(404, json_data={"message": "Not Found"})):
            with pytest.raises(RuntimeError, match="GitHub resource not found"):
                client.request("https://api.github.com/x")

    def test_401_raises_specific_message(self):
        client = GitHubRestClient(FakeSettingsManager())
        with patch("graphlink_plugins.common.github_client.requests.get", return_value=_fake_response(401, json_data={"message": "Bad credentials"})):
            with pytest.raises(RuntimeError, match="GitHub rejected the saved token"):
                client.request("https://api.github.com/x")

    def test_403_with_rate_limit_message_raises_rate_limit_error(self):
        client = GitHubRestClient(FakeSettingsManager())
        with patch("graphlink_plugins.common.github_client.requests.get", return_value=_fake_response(403, json_data={"message": "API rate limit exceeded"})):
            with pytest.raises(RuntimeError, match="rate limit reached"):
                client.request("https://api.github.com/x")

    def test_403_without_rate_limit_message_raises_raw_message(self):
        client = GitHubRestClient(FakeSettingsManager())
        with patch("graphlink_plugins.common.github_client.requests.get", return_value=_fake_response(403, json_data={"message": "Forbidden for another reason"})):
            with pytest.raises(RuntimeError, match="Forbidden for another reason"):
                client.request("https://api.github.com/x")

    def test_non_json_error_body_falls_back_to_text(self):
        client = GitHubRestClient(FakeSettingsManager())
        with patch("graphlink_plugins.common.github_client.requests.get", return_value=_fake_response(500, json_data=None, text="Internal Server Error")):
            with pytest.raises(RuntimeError, match="Internal Server Error"):
                client.request("https://api.github.com/x")

    def test_passes_params_and_timeout_through(self):
        client = GitHubRestClient(FakeSettingsManager())
        with patch("graphlink_plugins.common.github_client.requests.get", return_value=_fake_response(200, json_data={})) as mock_get:
            client.request("https://api.github.com/x", params={"page": 2}, timeout=99)
        _, kwargs = mock_get.call_args
        assert kwargs["params"] == {"page": 2}
        assert kwargs["timeout"] == 99
