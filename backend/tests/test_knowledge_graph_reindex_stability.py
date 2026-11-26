"""Unchanged graph content keeps its index rows, citations and embeddings (#1089)."""

from __future__ import annotations

import sqlite3
from contextlib import closing

import numpy as np

from backend import knowledge_store as ks

NODES = [("node-a", "Alpha node explains the cobalt restore protocol."),
         ("node-b", "Beta node lists the recovery host checklist.")]


def _save(db, nodes, *, graph_id=7, workspace_id=1, title="Runbook graph"):
    ks.reindex_graph_content(db, graph_id=graph_id, workspace_id=workspace_id, title=title, node_chunks=nodes)


def _rows(db):
    with closing(sqlite3.connect(db)) as conn:
        return {node: (doc, chunk) for doc, chunk, node in conn.execute(
            "SELECT document_id, id, source_node_id FROM chunks ORDER BY id")}


def _citation(db, node):
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT c.id AS chunk_id, c.document_id, c.text, c.offset_start, c.offset_end, c.source_node_id, "
            "d.source_uri FROM chunks c JOIN documents d ON d.id = c.document_id WHERE c.source_node_id = ?",
            (node,)).fetchone()
    return dict(row), ks._stable_chunk_citation(dict(row))


def _open(db, row, citation, *, collection):
    return ks.open_cited_document(db, document_id=row["document_id"], chunk_id=row["chunk_id"],
                                  citation_id=citation, collection_id=collection)


def _embed(db, chunk_ids):
    vector = np.asarray([1.0, 0.0], dtype=np.float32)
    ks.upsert_embeddings(db, "m", [(chunk_id, 2, vector.tobytes()) for chunk_id in chunk_ids])


def _embedded(db):
    with closing(sqlite3.connect(db)) as conn:
        return {row[0] for row in conn.execute("SELECT chunk_id FROM embeddings WHERE model_id = 'm'")}


def test_identical_and_geometry_only_saves_keep_rows_citations_and_embeddings(tmp_path):
    db = tmp_path / "knowledge.db"
    _save(db, NODES)
    before = _rows(db)
    _embed(db, [chunk for _, chunk in before.values()])
    row, citation = _citation(db, "node-a")
    collection = ks.get_or_create_workspace_collection(db, 1)
    _save(db, NODES)                          # identical save
    _save(db, NODES, title="Renamed graph")   # metadata/geometry-only change
    assert _rows(db) == before
    assert _embedded(db) == {chunk for _, chunk in before.values()}
    assert _open(db, row, citation, collection=collection)["excerpt"] == NODES[0][1]
    assert ks.get_document(db, before["node-a"][0])["title"] == "Renamed graph"


def test_one_changed_node_replaces_only_its_own_chunk(tmp_path):
    db = tmp_path / "knowledge.db"
    _save(db, NODES)
    before = _rows(db)
    _embed(db, [chunk for _, chunk in before.values()])
    old_b, old_b_citation = _citation(db, "node-b")
    collection = ks.get_or_create_workspace_collection(db, 1)
    _save(db, [NODES[0], ("node-b", "Beta node now lists a different checklist.")])
    after = _rows(db)
    assert after["node-a"] == before["node-a"]
    assert after["node-b"] != before["node-b"]
    assert _embedded(db) == {before["node-a"][1]}
    # The changed passage's old citation must not open the new text.
    assert _open(db, old_b, old_b_citation, collection=collection) is None


def test_removed_and_reordered_nodes(tmp_path):
    db = tmp_path / "knowledge.db"
    _save(db, NODES)
    before = _rows(db)
    old_a, old_a_citation = _citation(db, "node-a")
    collection = ks.get_or_create_workspace_collection(db, 1)
    _save(db, [NODES[1], NODES[0]])
    assert _rows(db) == before
    _save(db, [NODES[1]])
    assert set(_rows(db)) == {"node-b"}
    assert _open(db, old_a, old_a_citation, collection=collection) is None


def test_stale_ids_after_a_legitimate_reindex_still_open_the_same_passage_in_its_workspace(tmp_path):
    db = tmp_path / "knowledge.db"
    _save(db, NODES)
    row, citation = _citation(db, "node-a")
    collection = ks.get_or_create_workspace_collection(db, 1)
    other = ks.get_or_create_workspace_collection(db, 2)
    # Deleting and reindexing allocates fresh row IDs for the same text.
    ks.delete_graph_index(db, 7)
    _save(db, NODES)
    assert _rows(db)["node-a"] != (row["document_id"], row["chunk_id"])
    opened = _open(db, row, citation, collection=collection)
    assert opened is not None and opened["excerpt"] == NODES[0][1]
    # Never across workspaces, and never for a forged citation.
    assert _open(db, row, citation, collection=other) is None
    assert _open(db, row, "kb:" + "0" * 20, collection=collection) is None
