"""Phase 7 prerequisite increment 5 (the serializer model-mirror capstone):
PyCoderNode, CodeSandboxNode, and ArtifactNode get the WebNode.query /
HtmlViewNode.html_content treatment - plain model attributes kept in sync via
textChanged, so serializers.py (and a handful of other read sites found
during recon: graphlink_scene.find_items, graphlink_window's document-viewer
extraction, both nodes' preview_text() calls, and ArtifactNode's own
_on_tab_changed) stop reaching into live widgets.

A real, pre-existing data-loss bug fell out of this work, the same class of
bug increment 1 fixed for HtmlView: PyCoderNode/CodeSandboxNode's "analysis"
field was set via `ai_analysis_display.setHtml(markdown.markdown(text))`, and
the OLD serializer read it back via `ai_analysis_display.toPlainText()` - the
plain-text EXTRACTION of the rendered HTML, not the original markdown source.
Verified empirically before the fix: rendering "# Heading\n\n**bold**" and
reading it back this way returns "Heading\nbold" - headers, bold markers,
list bullets, and code fences are all silently discarded on every
save/reload. The fix stores the raw markdown source directly in a mirror
attribute at write-time, exactly like HtmlView's html_content.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from graphlink_plugins.graphlink_plugin_artifact import ArtifactNode
from graphlink_plugins.graphlink_plugin_code_sandbox import CodeSandboxNode
from graphlink_pycoder import PyCoderMode, PyCoderNode
from graphlink_scene import ChatScene
from graphlink_session.deserializers import SceneDeserializer
from graphlink_session.serializers import SceneSerializer

_MARKDOWN_ANALYSIS = "# Heading\n\nSome **bold** text and a list:\n\n- item one\n- item two"


def _make_window_and_scene():
    window = MagicMock()
    scene = ChatScene(window=window)
    window.chat_view.scene.return_value = scene
    return window, scene


def _desynced_widget_value(node, widget_attr, mirror_getter):
    """Sets the widget's text WITHOUT firing textChanged (blockSignals), then
    confirms the getter still returns the pre-existing mirror value rather
    than reflecting the widget - the decisive proof a getter reads the mirror
    and not the live widget."""
    widget = getattr(node, widget_attr)
    before = mirror_getter()
    widget.blockSignals(True)
    widget.setPlainText("SHOULD NOT BE VISIBLE THROUGH THE GETTER")
    widget.blockSignals(False)
    after = mirror_getter()
    return before, after


class TestPyCoderMirrorAttributes:
    def test_typing_into_prompt_updates_the_mirror(self):
        node = PyCoderNode(parent_node=None)
        node.prompt_input.setPlainText("calculate primes")
        assert node.get_prompt() == "calculate primes"

    def test_typing_into_code_updates_the_mirror(self):
        node = PyCoderNode(parent_node=None)
        node.code_input.setPlainText("print(1)")
        assert node.get_code() == "print(1)"

    def test_seed_prompt_updates_both_widget_and_mirror(self):
        node = PyCoderNode(parent_node=None)
        node.seed_prompt("seeded")
        assert node.get_prompt() == "seeded"
        assert node.prompt_input.toPlainText() == "seeded"

    def test_set_code_updates_both_widget_and_mirror(self):
        node = PyCoderNode(parent_node=None)
        node.set_code("print(2)")
        assert node.get_code() == "print(2)"
        assert node.code_input.toPlainText() == "print(2)"

    def test_set_output_updates_the_mirror(self):
        node = PyCoderNode(parent_node=None)
        node.set_output("42")
        assert node.get_output() == "42"

    def test_get_prompt_reads_the_mirror_not_the_live_widget(self):
        node = PyCoderNode(parent_node=None)
        node.seed_prompt("original")
        before, after = _desynced_widget_value(node, "prompt_input", node.get_prompt)
        assert before == after == "original"

    def test_get_output_reads_the_mirror_not_the_live_widget(self):
        node = PyCoderNode(parent_node=None)
        node.set_output("original output")
        before, after = _desynced_widget_value(node, "output_display", node.get_output)
        assert before == after == "original output"

    def test_set_ai_analysis_preserves_the_raw_markdown_source(self):
        # The decisive bug-fix test: the OLD behavior (reading
        # ai_analysis_display.toPlainText()) would have returned a mangled,
        # formatting-stripped string here, not the original markdown.
        node = PyCoderNode(parent_node=None)
        node.set_ai_analysis(_MARKDOWN_ANALYSIS)
        assert node.get_ai_analysis() == _MARKDOWN_ANALYSIS

    def test_get_ai_analysis_reads_the_mirror_not_the_rendered_widget(self):
        node = PyCoderNode(parent_node=None)
        node.set_ai_analysis(_MARKDOWN_ANALYSIS)
        # The widget holds RENDERED html, not plain text, so toPlainText() on
        # it would already differ from the source - confirm the getter never
        # goes near the widget at all.
        assert node.ai_analysis_display.toPlainText() != _MARKDOWN_ANALYSIS
        assert node.get_ai_analysis() == _MARKDOWN_ANALYSIS


class TestPyCoderSerializerRoundTrip:
    def test_full_round_trip_preserves_markdown_analysis_verbatim(self):
        window, scene = _make_window_and_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = PyCoderNode(parent, mode=PyCoderMode.MANUAL)
        node.set_code("print('hi')")
        node.set_output("hi")
        node.set_ai_analysis(_MARKDOWN_ANALYSIS)
        scene.addItem(node)
        scene.pycoder_nodes.append(node)

        parent_payload = SceneSerializer(window).serialize_node(parent, [parent, node])
        node_payload = SceneSerializer(window).serialize_node(node, [parent, node])
        assert node_payload["analysis"] == _MARKDOWN_ANALYSIS
        assert node_payload["output"] == "hi"

        target_window, target_scene = _make_window_and_scene()
        deserializer = SceneDeserializer(target_window)
        restored_parent = deserializer.deserialize_node(0, parent_payload, {})
        restored_node = deserializer.deserialize_node(1, node_payload, {0: restored_parent})

        assert restored_node.get_ai_analysis() == _MARKDOWN_ANALYSIS
        assert restored_node.get_output() == "hi"
        assert restored_node.get_code() == "print('hi')"


class TestCodeSandboxMirrorAttributes:
    def test_typing_into_prompt_updates_the_mirror(self):
        node = CodeSandboxNode(parent_node=None)
        node.prompt_input.setPlainText("build a scraper")
        assert node.get_prompt() == "build a scraper"

    def test_typing_into_requirements_updates_the_mirror_and_strips(self):
        node = CodeSandboxNode(parent_node=None)
        node.requirements_input.setPlainText("  pandas\nnumpy  ")
        assert node.get_requirements() == "pandas\nnumpy"

    def test_typing_into_code_updates_the_mirror(self):
        node = CodeSandboxNode(parent_node=None)
        node.code_input.setPlainText("import pandas")
        assert node.get_code() == "import pandas"

    def test_set_output_updates_the_mirror(self):
        node = CodeSandboxNode(parent_node=None)
        node.set_output("done")
        assert node.get_output() == "done"

    def test_clear_terminal_output_clears_the_mirror(self):
        node = CodeSandboxNode(parent_node=None)
        node.set_output("stale output")
        node.clear_terminal_output()
        assert node.get_output() == ""

    def test_append_terminal_output_accumulates_in_the_mirror(self):
        node = CodeSandboxNode(parent_node=None)
        node.clear_terminal_output()
        node.append_terminal_output("line 1\n")
        node.append_terminal_output("line 2\n")
        assert node.get_output() == "line 1\nline 2\n"

    def test_get_requirements_reads_the_mirror_not_the_live_widget(self):
        node = CodeSandboxNode(parent_node=None)
        node.set_requirements("pandas")
        before, after = _desynced_widget_value(node, "requirements_input", node.get_requirements)
        assert before == after == "pandas"

    def test_set_ai_analysis_preserves_the_raw_markdown_source(self):
        node = CodeSandboxNode(parent_node=None)
        node.set_ai_analysis(_MARKDOWN_ANALYSIS)
        assert node.get_ai_analysis() == _MARKDOWN_ANALYSIS
        assert node.ai_analysis_display.toPlainText() != _MARKDOWN_ANALYSIS


class TestCodeSandboxSerializerRoundTrip:
    def test_full_round_trip_preserves_markdown_analysis_and_streamed_output(self):
        window, scene = _make_window_and_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = CodeSandboxNode(parent)
        node.set_requirements("pandas\nnumpy")
        node.set_code("import pandas as pd")
        node.clear_terminal_output()
        node.append_terminal_output("Building venv...\n")
        node.append_terminal_output("Done.\n")
        node.set_ai_analysis(_MARKDOWN_ANALYSIS)
        scene.addItem(node)
        scene.code_sandbox_nodes.append(node)

        parent_payload = SceneSerializer(window).serialize_node(parent, [parent, node])
        node_payload = SceneSerializer(window).serialize_node(node, [parent, node])
        assert node_payload["output"] == "Building venv...\nDone.\n"
        assert node_payload["analysis"] == _MARKDOWN_ANALYSIS
        assert node_payload["requirements"] == "pandas\nnumpy"

        target_window, target_scene = _make_window_and_scene()
        deserializer = SceneDeserializer(target_window)
        restored_parent = deserializer.deserialize_node(0, parent_payload, {})
        restored_node = deserializer.deserialize_node(1, node_payload, {0: restored_parent})

        assert restored_node.get_output() == "Building venv...\nDone.\n"
        assert restored_node.get_ai_analysis() == _MARKDOWN_ANALYSIS
        assert restored_node.get_requirements() == "pandas\nnumpy"


class TestArtifactMirrorAttributes:
    def test_typing_into_instruction_updates_the_mirror(self):
        node = ArtifactNode(parent_node=None)
        node.instruction_input.setPlainText("expand section 2")
        assert node.get_instruction() == "expand section 2"

    def test_typing_into_raw_editor_updates_the_mirror(self):
        node = ArtifactNode(parent_node=None)
        node.raw_editor.setPlainText("# Draft\n\nBody text.")
        assert node.get_artifact_content() == "# Draft\n\nBody text."

    def test_seed_prompt_updates_both_widget_and_mirror(self):
        node = ArtifactNode(parent_node=None)
        node.seed_prompt("seeded instruction")
        assert node.get_instruction() == "seeded instruction"
        assert node.instruction_input.toPlainText() == "seeded instruction"

    def test_set_artifact_content_updates_both_widget_and_mirror(self):
        node = ArtifactNode(parent_node=None)
        node.set_artifact_content("new content")
        assert node.get_artifact_content() == "new content"
        assert node.raw_editor.toPlainText() == "new content"

    def test_get_artifact_content_reads_the_mirror_not_the_live_widget(self):
        node = ArtifactNode(parent_node=None)
        node.set_artifact_content("original content")
        before, after = _desynced_widget_value(node, "raw_editor", node.get_artifact_content)
        assert before == after == "original content"

    def test_switching_to_preview_tab_renders_the_mirror_not_a_desynced_widget(self):
        # _on_tab_changed used to read raw_editor.toPlainText() directly; now
        # it goes through get_artifact_content(). Desync the widget from the
        # mirror (blockSignals) and confirm the preview still renders the
        # mirror's content, not whatever is live in the widget.
        node = ArtifactNode(parent_node=None)
        node.set_artifact_content("# Real Content")
        node.raw_editor.blockSignals(True)
        node.raw_editor.setPlainText("SHOULD NOT BE RENDERED")
        node.raw_editor.blockSignals(False)

        node._on_tab_changed(1)

        assert "Real Content" in node.preview_display.toPlainText()
        assert "SHOULD NOT BE RENDERED" not in node.preview_display.toPlainText()


class TestArtifactSerializerRoundTrip:
    def test_full_round_trip_preserves_instruction_and_content(self):
        window, scene = _make_window_and_scene()
        parent = scene.add_chat_node("parent", is_user=True)
        node = ArtifactNode(parent)
        node.seed_prompt("write a summary")
        node.set_artifact_content("# Summary\n\n- point one\n- point two")
        scene.addItem(node)
        scene.artifact_nodes.append(node)

        parent_payload = SceneSerializer(window).serialize_node(parent, [parent, node])
        node_payload = SceneSerializer(window).serialize_node(node, [parent, node])
        assert node_payload["content"] == "# Summary\n\n- point one\n- point two"

        target_window, target_scene = _make_window_and_scene()
        deserializer = SceneDeserializer(target_window)
        restored_parent = deserializer.deserialize_node(0, parent_payload, {})
        restored_node = deserializer.deserialize_node(1, node_payload, {0: restored_parent})

        assert restored_node.get_artifact_content() == "# Summary\n\n- point one\n- point two"


class TestFindItemsUsesGettersNotWidgets:
    """graphlink_scene.find_items (the search overlay's backing search) used to
    read output_display.toPlainText() directly for PyCoder/CodeSandbox - now
    goes through get_output()."""

    def test_search_matches_pycoder_output_via_the_mirror(self):
        window = MagicMock()
        scene = ChatScene(window=window)
        parent = scene.add_chat_node("parent", is_user=True)
        node = PyCoderNode(parent)
        node.set_output("a very particular needle")
        scene.addItem(node)
        scene.pycoder_nodes.append(node)

        assert node in scene.find_items("particular needle")

    def test_search_matches_code_sandbox_output_via_the_mirror(self):
        window = MagicMock()
        scene = ChatScene(window=window)
        parent = scene.add_chat_node("parent", is_user=True)
        node = CodeSandboxNode(parent)
        node.set_output("another unique needle")
        scene.addItem(node)
        scene.code_sandbox_nodes.append(node)

        assert node in scene.find_items("unique needle")
