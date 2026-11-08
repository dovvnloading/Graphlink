"""The scene wire publishes typed edge roles in full snapshots and patches."""

from backend.canvas import SceneDocument


def test_scene_payload_preserves_edge_role():
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    child = doc.add_chat_node(0, 300, "child", False, parent_id=root.id)
    edge = next(e for e in doc.edges.values() if e.target == child.id)
    doc.set_edge_role(edge.id, "primary")

    assert doc.scene_payload()["edges"] == [
        {"id": edge.id, "source": root.id, "target": child.id, "role": "primary"}
    ]


def test_edge_role_edit_emits_an_upsert_edge_patch():
    doc = SceneDocument()
    root = doc.add_chat_node(0, 0, "root", True)
    child = doc.add_chat_node(0, 300, "child", False, parent_id=root.id)
    edge = next(e for e in doc.edges.values() if e.target == child.id)
    assert doc.take_dirty_patch_ops() is None

    doc.set_edge_role(edge.id, "primary")
    ops = doc.take_dirty_patch_ops()
    assert ops is not None
    assert [op for op in ops if op["op"] == "upsertEdge"] == [
        {"op": "upsertEdge", "edge": {
            "id": edge.id, "source": root.id, "target": child.id, "role": "primary"
        }}
    ]
