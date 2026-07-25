"""Phase 7 prerequisite increment 5 (the serializer model-mirror capstone) covered
PyCoderNode, CodeSandboxNode, and ArtifactNode getting the WebNode.query /
HtmlViewNode.html_content treatment - plain model attributes kept in sync via
textChanged, so serializers.py and a handful of other read sites stopped reaching
into live widgets - plus the serializer round-trip fidelity and getter-based
read-site coverage (find_items, chart-context extraction, deserializer restore)
that went with it.

Qt-removal update: PyCoderNode, CodeSandboxNode, and ArtifactNode were all removed
as part of the Qt-to-web rewrite (superseded by Qt-free FastAPI+React
implementations). Every test case in this file existed solely to exercise the
mirror-attribute/serializer-round-trip contract for those three classes - no other
node type (ConversationNode, HtmlViewNode, ChatNode, CodeNode, DocumentNode,
ImageNode, ThinkingNode) was covered here, so there is nothing left to keep. The
file is retained (not deleted) per the Qt-removal cleanup convention, empty of test
cases.
"""
