"""ADR-019 stage 19.1: deterministic synthetic graph fixtures.

Builds real `SceneDocument` instances entirely through the same production
API the app itself uses to create nodes (`add_chat_node`, `add_chart_node`,
`add_image_node`, `connect`) - never by hand-constructing `SceneNode`
directly - so a fixture can never silently drift from what the app actually
persists/serializes. Every fixture is fully deterministic (no randomness):
the same call always produces byte-identical node/edge ids and content, so
a measurement taken today is directly comparable to one taken after a
later-landing ADR without also having to account for fixture drift.

The four reference workloads match ADR-019's budget table exactly:
`SMALL`, `TYPICAL`, `LARGE`, `STRESS`. Node topology is a deterministic
binary tree (node i's parent is node (i-1)//2) plus a fixed number of extra
cross-links - not a flat chain - because a flat chain under-costs
`toFlowNodes`'s real O(N*E) edge-scan behavior (ADR-011 P2) relative to how
a real branching conversation graph looks.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.canvas import SceneDocument

_LOREM = (
    "The quick brown fox jumps over the lazy dog while considering the "
    "architecture of a distributed system. "
)


def _content_of_length(min_bytes: int) -> str:
    reps = (min_bytes // len(_LOREM)) + 1
    return (_LOREM * reps)[:min_bytes]


def build_graph(
    node_count: int,
    *,
    content_bytes: int = 1200,
    chart_count: int = 0,
    image_count: int = 0,
    extra_edges: int = 0,
) -> SceneDocument:
    """Builds a deterministic branching-tree SceneDocument with exactly
    node_count nodes TOTAL - matching ADR-019's own workload table, which
    reads e.g. "100 nodes... 2 charts, 1 image" as the charts/image being
    PART OF the 100, not additional to it. chart_count of those nodes are
    chart nodes and image_count are image nodes, grafted onto existing chat
    nodes at fixed, evenly-spaced positions; the rest
    (node_count - chart_count - image_count) are the chat-node tree itself.
    extra_edges adds that many additional cross-links beyond the chat
    tree's own N-1 edges."""
    if node_count < 1:
        raise ValueError("node_count must be >= 1")
    chat_count = node_count - chart_count - image_count
    if chat_count < 1:
        raise ValueError("chart_count + image_count must leave at least 1 chat node")

    doc = SceneDocument()
    content = _content_of_length(content_bytes)

    ids: list[str] = []
    root = doc.add_chat_node(0.0, 0.0, content, is_user=False)
    ids.append(root.id)

    for i in range(1, chat_count):
        parent_index = (i - 1) // 2  # deterministic binary-tree branching
        parent_id = ids[parent_index]
        is_user = i % 2 == 1
        node = doc.add_chat_node(
            float((i % 20) * 260),
            float((i // 20) * 160),
            content,
            is_user=is_user,
            parent_id=parent_id,
        )
        ids.append(node.id)

    if chart_count > 0:
        stride = max(1, chat_count // chart_count)
        for k in range(chart_count):
            parent_id = ids[(k * stride) % chat_count]
            doc.add_chart_node(
                100.0, 100.0, parent_id, "bar",
                {"labels": ["a", "b", "c"], "values": [1.0, 2.0, 3.0]},
            )

    if image_count > 0:
        stride = max(1, chat_count // image_count)
        # 64 bytes of placeholder PNG-shaped bytes is enough to exercise the
        # image_assets store's bookkeeping cost without needing a real PNG
        # decode anywhere in the payload/serialize path being measured.
        placeholder = b"\x89PNG\r\n\x1a\n" + b"\x00" * 56
        for k in range(image_count):
            parent_id = ids[(k * stride) % chat_count]
            doc.add_image_node(150.0, 150.0, placeholder, "a generated image", parent_id)

    added = 0
    shift = max(2, chat_count // 10)
    i = 0
    while added < extra_edges and i + shift < chat_count:
        doc.connect(ids[i], ids[i + shift])
        added += 1
        i += 1

    return doc


@dataclass(frozen=True)
class Workload:
    name: str
    node_count: int
    content_bytes: int
    chart_count: int
    image_count: int
    extra_edges: int

    def build(self) -> SceneDocument:
        return build_graph(
            self.node_count,
            content_bytes=self.content_bytes,
            chart_count=self.chart_count,
            image_count=self.image_count,
            extra_edges=self.extra_edges,
        )


# ADR-019 SS1: the four reference workloads, values matched to the ADR's own
# table (25/100/500/2,000 nodes; typical/large also carry charts+images).
SMALL = Workload("small", node_count=25, content_bytes=400, chart_count=0, image_count=0, extra_edges=5)
TYPICAL = Workload("typical", node_count=100, content_bytes=1200, chart_count=2, image_count=1, extra_edges=40)
LARGE = Workload("large", node_count=500, content_bytes=1200, chart_count=10, image_count=5, extra_edges=200)
STRESS = Workload("stress", node_count=2000, content_bytes=1200, chart_count=0, image_count=0, extra_edges=0)

ALL_WORKLOADS = (SMALL, TYPICAL, LARGE, STRESS)
