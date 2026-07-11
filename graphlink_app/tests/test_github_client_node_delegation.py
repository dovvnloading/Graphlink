"""Tests that GitlinkNode actually delegates to GitHubRestClient.

test_github_client.py covers the client's own logic in isolation; this file guards
against the node class silently reverting to its own hand-rolled implementation (or the
wrapper methods and the client instance drifting apart) since that would quietly
reintroduce the exact duplication Phase 3a removed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphlink_plugins.common.github_client import GitHubRestClient
from graphlink_plugins.graphlink_plugin_gitlink import GitlinkNode


class FakeSettingsManager:
    def get_github_token(self):
        return "shared-token"


def test_gitlink_node_uses_a_github_rest_client_instance():
    node = GitlinkNode(parent_node=None)
    assert isinstance(node._github_client, GitHubRestClient)


def test_gitlink_node_token_comes_from_the_shared_client():
    node = GitlinkNode(parent_node=None, settings_manager=FakeSettingsManager())
    assert node._get_github_token() == "shared-token"
    assert node._get_github_token() == node._github_client.get_token()


def test_gitlink_node_github_request_calls_through_to_the_client_with_kwargs():
    node = GitlinkNode(parent_node=None)
    with patch.object(node._github_client, "request", return_value=b"raw") as mock_request:
        result = node._github_request("https://api.github.com/x", params={"a": 1}, expect_json=False, timeout=5)
    mock_request.assert_called_once_with("https://api.github.com/x", {"a": 1}, expect_json=False, timeout=5)
    assert result == b"raw"


def test_gitlink_node_headers_include_the_token():
    gl = GitlinkNode(parent_node=None, settings_manager=FakeSettingsManager())
    assert gl._github_headers()["Authorization"] == "Bearer shared-token"
