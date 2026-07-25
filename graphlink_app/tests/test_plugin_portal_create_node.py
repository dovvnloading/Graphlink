"""Tests for PluginPortal.create_node() and its first consumer, _create_artifact_node.

NOTE (Qt removal cleanup): graphlink_plugin_artifact.py (ArtifactNode,
ArtifactConnectionItem) was deleted as part of the Qt-free artifact/drafter plugin
rewrite (see backend/, web_ui/), and PluginPortal._create_artifact_node was removed
along with it. Every test that used to live in this file exercised create_node()
exclusively through that factory - including the generic
"no_selection_message used when invalid_parent_message omitted" case at the bottom,
which used ArtifactNode/ArtifactConnectionItem purely as its concrete node/connection
classes rather than testing anything Artifact-specific - so none of it can run against
a deleted class, and all test cases were removed rather than adapted.

Generic create_node() coverage now lives in
tests/test_plugin_portal_remaining_factories.py (exercised via the still-live
Conversation Node and HTML Renderer factories). Artifact plugin coverage lives in the
new FastAPI+React implementation's own test suite.
"""
