"""Tests that CodeReviewNode and GitlinkNode actually delegate to GitHubRestClient.

test_github_client.py covers the client's own logic in isolation; this file guards
against the two node classes silently reverting to their own hand-rolled
implementations (or the wrapper methods and the client instance drifting apart) since
that would quietly reintroduce the exact duplication Phase 3a removed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphite_plugins.common.github_client import GitHubRestClient
from graphite_plugins.graphite_plugin_code_review import CodeReviewNode
from graphite_plugins.graphite_plugin_gitlink import GitlinkNode


class FakeSettingsManager:
    def get_github_token(self):
        return "shared-token"


def test_code_review_node_uses_a_github_rest_client_instance():
    node = CodeReviewNode(parent_node=None)
    assert isinstance(node._github_client, GitHubRestClient)


def test_gitlink_node_uses_a_github_rest_client_instance():
    node = GitlinkNode(parent_node=None)
    assert isinstance(node._github_client, GitHubRestClient)


def test_code_review_node_token_comes_from_the_shared_client():
    node = CodeReviewNode(parent_node=None, settings_manager=FakeSettingsManager())
    assert node._get_github_token() == "shared-token"
    assert node._get_github_token() == node._github_client.get_token()


def test_gitlink_node_token_comes_from_the_shared_client():
    node = GitlinkNode(parent_node=None, settings_manager=FakeSettingsManager())
    assert node._get_github_token() == "shared-token"
    assert node._get_github_token() == node._github_client.get_token()


def test_code_review_node_github_request_calls_through_to_the_client():
    node = CodeReviewNode(parent_node=None)
    with patch.object(node._github_client, "request", return_value={"ok": True}) as mock_request:
        result = node._github_request("https://api.github.com/x", params={"a": 1})
    mock_request.assert_called_once_with("https://api.github.com/x", {"a": 1})
    assert result == {"ok": True}


def test_gitlink_node_github_request_calls_through_to_the_client_with_kwargs():
    node = GitlinkNode(parent_node=None)
    with patch.object(node._github_client, "request", return_value=b"raw") as mock_request:
        result = node._github_request("https://api.github.com/x", params={"a": 1}, expect_json=False, timeout=5)
    mock_request.assert_called_once_with("https://api.github.com/x", {"a": 1}, expect_json=False, timeout=5)
    assert result == b"raw"


def test_both_nodes_produce_identical_headers_for_the_same_token():
    # Guards against the two files' delegation drifting into subtly different header
    # shapes now that the underlying logic is shared.
    cr = CodeReviewNode(parent_node=None, settings_manager=FakeSettingsManager())
    gl = GitlinkNode(parent_node=None, settings_manager=FakeSettingsManager())
    assert cr._github_headers() == gl._github_headers()
