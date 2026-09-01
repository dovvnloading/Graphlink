"""ADR-009 stages 9.5 and 9.6: the two on-disk format changes.

9.5 externalizes image bytes into the content-addressed asset store, so
autosave stops rewriting megabytes of base64 every tick for a picture that
has not changed. 9.6 makes a flat `edges` list the authoritative edge
record, retiring the 14-bucket classification this backend can only guess
at on the way out and can only approximate on the way back in.

Both are WRITE-NEW / READ-BOTH, and that is the property most of these
tests are actually defending: a chat written by an older build must keep
loading byte-for-byte untouched, because the alternative is a destructive
migration over data nobody can get back if it goes wrong. So each stage
gets the same three claims - the new shape is written, the new shape round
trips, and the OLD shape still loads with the new code.
"""

from __future__ import annotations

import backend.agents as agents_module  # noqa: F401 - see test_canvas.py's own import-order note
from backend.asset_store import AssetStore, content_ref
from backend.canvas import SceneDocument
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data

PNG = b"\x89PNG\r\n\x1a\n" + b"pretend pixels" * 64


def _round_trip(doc: SceneDocument, *, save_store=None, load_store=None) -> SceneDocument:
    chat_data = build_chat_data(doc, asset_store=save_store)
    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")
    restored = SceneDocument()
    restore_chat_into_document(
        restored, {"data": chat_data}, notes_data, pins_data, asset_store=load_store
    )
    return restored


def _has_edge(document: SceneDocument, source_id: str, target_id: str) -> bool:
    return any(e.source == source_id and e.target == target_id for e in document.edges.values())


def _image_doc() -> SceneDocument:
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "draw me a cat", is_user=True)
    doc.add_image_node(10, 10, PNG, "a cat", parent.id, mime_type="image/png")
    return doc


def _only_image_payload(chat_data: dict) -> dict:
    return next(n for n in chat_data["nodes"] if n["node_type"] == "image")


# -- 9.5: image bytes move to the asset store --------------------------------


def test_with_an_asset_store_the_payload_carries_a_ref_and_no_inline_bytes(tmp_path):
    # The entire point of the stage: the megabytes leave the chat row.
    store = AssetStore(tmp_path / "assets")
    chat_data = build_chat_data(_image_doc(), asset_store=store)

    payload = _only_image_payload(chat_data)
    assert payload["asset_ref"] == content_ref(PNG)
    assert "image_bytes" not in payload, "bytes were inlined despite a store being available"
    assert store.get(payload["asset_ref"]) == PNG


def test_an_externalized_image_round_trips_back_into_a_document(tmp_path):
    store = AssetStore(tmp_path / "assets")
    restored = _round_trip(_image_doc(), save_store=store, load_store=store)

    image = next(n for n in restored.nodes.values() if n.kind == "image")
    assert restored.image_assets[image.state.image_asset_id] == (PNG, "image/png")
    assert image.content == "a cat"


def test_saving_the_same_unchanged_image_twice_stores_the_bytes_once(tmp_path):
    # The autosave case this stage exists for: tick two must not write a
    # second copy of an image that did not change.
    store = AssetStore(tmp_path / "assets")
    doc = _image_doc()
    first = _only_image_payload(build_chat_data(doc, asset_store=store))
    second = _only_image_payload(build_chat_data(doc, asset_store=store))

    assert first["asset_ref"] == second["asset_ref"]
    stored_files = [p for p in (tmp_path / "assets").rglob("*") if p.is_file()]
    assert len(stored_files) == 1, f"content addressing did not dedupe: {stored_files}"


def test_without_a_store_the_historical_inline_shape_is_written_unchanged():
    # Every existing caller passes no store. They must keep getting the
    # exact payload they got before this stage existed.
    payload = _only_image_payload(build_chat_data(_image_doc()))

    assert "asset_ref" not in payload
    assert isinstance(payload["image_bytes"], str) and payload["image_bytes"]


def test_a_chat_saved_before_this_stage_still_loads_with_the_new_code(tmp_path):
    # READ-BOTH, the claim that makes this non-destructive: inline bytes
    # written by an older build load even when a store IS supplied.
    legacy = build_chat_data(_image_doc())  # no store -> inline base64
    notes_data = legacy.pop("notes_data")
    pins_data = legacy.pop("pins_data")

    restored = SceneDocument()
    restore_chat_into_document(
        restored, {"data": legacy}, notes_data, pins_data,
        asset_store=AssetStore(tmp_path / "assets"),
    )

    image = next(n for n in restored.nodes.values() if n.kind == "image")
    assert restored.image_assets[image.state.image_asset_id][0] == PNG


def test_a_ref_the_store_lost_costs_the_picture_not_the_conversation(tmp_path):
    # A missing asset must degrade to "no image", never to a failed load.
    store = AssetStore(tmp_path / "assets")
    chat_data = build_chat_data(_image_doc(), asset_store=store)
    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")

    empty_store = AssetStore(tmp_path / "elsewhere")
    restored = SceneDocument()
    restore_chat_into_document(
        restored, {"data": chat_data}, notes_data, pins_data, asset_store=empty_store
    )

    image = next(n for n in restored.nodes.values() if n.kind == "image")
    assert image.state.image_asset_id == ""
    assert image.content == "a cat", "the surrounding conversation must survive intact"


def test_a_failed_save_does_not_leave_the_store_visible_to_the_next_one():
    # build_chat_data publishes the store through a contextvar; a raising
    # save must reset it, or the next storeless save silently externalizes.
    class Exploding:
        def put(self, data):
            raise RuntimeError("simulated store failure")

    try:
        build_chat_data(_image_doc(), asset_store=Exploding())
    except RuntimeError:
        pass

    payload = _only_image_payload(build_chat_data(_image_doc()))
    assert "asset_ref" not in payload, "a stale store leaked into the next save"


# -- 9.6: the flat edge list -------------------------------------------------


def _connected_doc() -> tuple[SceneDocument, list[tuple[str, str]]]:
    """A document whose edges span every shape the legacy buckets split
    across: a structural parent edge, a note->chat (system prompt), a
    chat->note (group summary), and a plain user-drawn connection."""
    doc = SceneDocument()
    a = doc.add_chat_node(0, 0, "question", is_user=True)
    b = doc.add_chat_node(100, 0, "answer", is_user=False)
    doc.add_code_node(200, 0, "x = 1", "python", b.id)  # structural parent edge
    system = doc.add_note(0, -100, is_system_prompt=True)
    summary = doc.add_note(300, 100, is_summary_note=True)
    doc.connect(system.id, a.id)
    doc.connect(b.id, summary.id)
    doc.connect(a.id, b.id)  # the plain catch-all connection
    return doc, [(e.source, e.target) for e in doc.edges.values()]


def test_the_flat_edge_list_records_every_edge_by_persistent_id():
    doc, expected = _connected_doc()
    chat_data = build_chat_data(doc)

    written = [(e["source"], e["target"]) for e in chat_data["edges"]]
    assert sorted(written) == sorted(expected)


def test_every_edge_survives_a_round_trip_including_note_and_chart_endpoints():
    doc, expected = _connected_doc()
    restored = _round_trip(doc)

    assert len(restored.edges) == len(expected), (
        f"edge count changed across the round trip: {len(expected)} -> {len(restored.edges)}"
    )
    # Same shape, translated to the restored document's own ids.
    kinds = lambda d, pairs: sorted(  # noqa: E731 - local, reads better inline
        (d.nodes[s].kind, d.nodes[t].kind) for s, t in pairs
    )
    restored_pairs = [(e.source, e.target) for e in restored.edges.values()]
    assert kinds(restored, restored_pairs) == kinds(doc, expected)


def test_a_chart_edge_resolves_because_charts_are_in_the_endpoint_map():
    # Charts live outside the "nodes" list entirely, so a flat edge naming
    # one only resolves if the load side merges charts_by_id in. This is
    # the case a nodes-only endpoint map would silently drop.
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "plot it", is_user=False)
    # "type" must live inside the data dict: that is what _restore_charts
    # reads the chart type back off (the constructor argument is not
    # persisted separately), so a fixture without it silently round-trips
    # to nothing.
    chart = doc.add_chart_node(
        50, 50, parent.id, "bar", {"type": "bar", "labels": ["a"], "values": [1]}
    )
    assert _has_edge(doc, parent.id, chart.id)

    restored = _round_trip(doc)

    new_parent = next(n for n in restored.nodes.values() if n.kind == "chat")
    new_chart = next(n for n in restored.nodes.values() if n.kind == "chart")
    assert _has_edge(restored, new_parent.id, new_chart.id)


def test_a_file_written_before_this_stage_falls_back_to_the_legacy_buckets():
    # The compatibility claim: strip `edges` (exactly what an older build's
    # payload looks like) and the 14-bucket reconstruction still runs.
    doc, _ = _connected_doc()
    chat_data = build_chat_data(doc)
    del chat_data["edges"]
    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")

    restored = SceneDocument()
    restore_chat_into_document(restored, {"data": chat_data}, notes_data, pins_data)

    assert len(restored.edges) == len(doc.edges), "the legacy fallback path regressed"


def test_an_edge_naming_a_node_that_no_longer_exists_is_skipped_not_fatal():
    doc, _ = _connected_doc()
    chat_data = build_chat_data(doc)
    chat_data["edges"].append({"source": "ghost-node", "target": "also-gone"})
    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")

    restored = SceneDocument()
    restore_chat_into_document(restored, {"data": chat_data}, notes_data, pins_data)

    assert len(restored.edges) == len(doc.edges)


def test_a_duplicate_entry_does_not_create_a_second_parallel_edge():
    # The flat list deliberately re-asserts edges the restore loops already
    # made; that is only safe because connect() is idempotent. Pin it.
    doc, _ = _connected_doc()
    chat_data = build_chat_data(doc)
    chat_data["edges"] = chat_data["edges"] + chat_data["edges"]
    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")

    restored = SceneDocument()
    restore_chat_into_document(restored, {"data": chat_data}, notes_data, pins_data)

    assert len(restored.edges) == len(doc.edges)


def test_the_legacy_buckets_are_still_written_for_older_readers():
    # Retiring the READ side is this stage; dropping the write side would
    # break older builds and the legacy app's own reader, so it is
    # deliberately not done. If someone removes them, this should be a
    # conscious decision, not a silent side effect.
    chat_data = build_chat_data(_connected_doc()[0])

    assert "connections" in chat_data
    assert chat_data["system_prompt_connections"], "system-prompt bucket stopped being written"
    assert chat_data["group_summary_connections"], "group-summary bucket stopped being written"


# -- ref validation: a ref names content, it is never a path ---------------


def test_store_rejects_a_ref_that_is_not_a_content_digest(tmp_path):
    """A ref is read straight out of persisted rows and imported archives -
    data this process did not necessarily write - so a crafted value must
    never be trusted into `root / ref[:2] / ref`. Only a SHA-256 hex digest
    (exactly what content_ref produces) resolves to a path at all."""
    store = AssetStore(tmp_path / "assets")
    (tmp_path / "secret.txt").write_bytes(b"TOP SECRET")
    ref = store.put(b"real image bytes")
    assert store.get(ref) == b"real image bytes"

    for crafted in ("../secret.txt", "../../secret.txt", r"..\secret.txt", "", "x", ref.upper()):
        assert store.get(crafted) is None, crafted
        assert store.exists(crafted) is False, crafted
        assert store.verify(crafted) is False, crafted


# -- note edges survive a real DB round-trip (not just the in-memory one) --


def test_an_uncoloured_note_stays_uncoloured_across_a_db_round_trip(tmp_path):
    """SceneNode.color's contract is "None means use the kind's own default
    colour", resolved by the frontend. The save path used to override that
    with a hard-coded "#4a7c59", so a note nobody ever coloured came back
    from a save/reload permanently green - and in a colour absent from the
    picker's own palette, so it could not be chosen or re-chosen either."""
    from backend.chat_library import (
        load_chat_row,
        load_notes_rows,
        load_pins_rows,
        save_chat_atomically_row,
    )

    db_path = tmp_path / "chats.db"
    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "hello", is_user=True)
    note = doc.add_note(0, -120)
    doc.set_note_content(note.id, "a plain note")
    doc.connect(note.id, chat.id)
    assert doc.nodes[note.id].color is None, "precondition: nothing chose a colour"

    chat_data = build_chat_data(doc)
    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")
    chat_id, _ = save_chat_atomically_row(db_path, None, "t", chat_data, notes_data, pins_data)

    # Reloaded exactly as chat_library.loadChat does - through the notes
    # table, not from the in-memory notes_data above.
    restored = SceneDocument()
    restore_chat_into_document(
        restored,
        load_chat_row(db_path, chat_id),
        load_notes_rows(db_path, chat_id),
        load_pins_rows(db_path, chat_id),
    )

    restored_note = next(n for n in restored.nodes.values() if n.kind == "note")
    assert restored_note.color is None


def test_a_chosen_note_colour_survives_a_db_round_trip(tmp_path):
    """The other half: normalising the old default must not flatten a real
    choice. The picker's own Green is #3f8f5c, a different value."""
    from backend.chat_library import (
        load_chat_row,
        load_notes_rows,
        load_pins_rows,
        save_chat_atomically_row,
    )

    db_path = tmp_path / "chats.db"
    doc = SceneDocument()
    doc.add_chat_node(0, 0, "hello", is_user=True)
    note = doc.add_note(0, -120)
    doc.set_group_color(note.id, "#3f8f5c", None)

    chat_data = build_chat_data(doc)
    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")
    chat_id, _ = save_chat_atomically_row(db_path, None, "t", chat_data, notes_data, pins_data)

    # Reloaded exactly as chat_library.loadChat does - through the notes
    # table, not from the in-memory notes_data above.
    restored = SceneDocument()
    restore_chat_into_document(
        restored,
        load_chat_row(db_path, chat_id),
        load_notes_rows(db_path, chat_id),
        load_pins_rows(db_path, chat_id),
    )

    restored_note = next(n for n in restored.nodes.values() if n.kind == "note")
    assert restored_note.color == "#3f8f5c"


def test_a_legacy_note_row_carrying_the_old_forced_default_loads_uncoloured(tmp_path):
    """Rows written before the fix carry the forced default whether or not
    anyone chose it. Normalising on READ means they render neutral without a
    destructive migration over an existing database."""
    from backend.chat_library import (
        load_chat_row,
        load_notes_rows,
        load_pins_rows,
        save_chat_atomically_row,
    )

    db_path = tmp_path / "chats.db"
    doc = SceneDocument()
    doc.add_chat_node(0, 0, "hello", is_user=True)
    doc.add_note(0, -120)

    chat_data = build_chat_data(doc)
    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")
    # Exactly what the pre-fix save path wrote.
    notes_data[0]["color"] = "#4a7c59"
    chat_id, _ = save_chat_atomically_row(db_path, None, "t", chat_data, notes_data, pins_data)

    # Reloaded exactly as chat_library.loadChat does - through the notes
    # table, not from the in-memory notes_data above.
    restored = SceneDocument()
    restore_chat_into_document(
        restored,
        load_chat_row(db_path, chat_id),
        load_notes_rows(db_path, chat_id),
        load_pins_rows(db_path, chat_id),
    )

    restored_note = next(n for n in restored.nodes.values() if n.kind == "note")
    assert restored_note.color is None


def test_note_edges_survive_a_full_db_round_trip(tmp_path):
    """Regression for the note-edge data-loss bug.

    The existing round-trip tests feed notes_data (payload ids intact)
    straight into restore_chat_into_document, which HIDES the defect: on the
    real path notes_data is written to the `notes` DB TABLE and read back by
    load_notes_rows, and that table had no column for the note's payload id.
    So the flat `edges` list (authoritative since stage 9.6) could not
    resolve any note endpoint on load, and every note connection - a System
    Prompt note attached to a chat, a chat->summary note, a user-drawn note
    link - was silently dropped, then written back gone on the next save.

    This test therefore drives the REAL save/load path through the SQLite
    row + notes table, the one place the id was being stripped."""
    from backend.chat_library import (
        load_chat_row,
        load_notes_rows,
        load_pins_rows,
        save_chat_atomically_row,
    )

    db_path = tmp_path / "chats.db"

    doc = SceneDocument()
    chat = doc.add_chat_node(0, 0, "hello", is_user=True)
    note = doc.add_note(0, -120, is_system_prompt=True)
    doc.set_note_content(note.id, "You are a helpful assistant.")
    doc.connect(note.id, chat.id)  # the system-prompt note -> chat edge
    assert _has_edge(doc, note.id, chat.id)

    chat_data = build_chat_data(doc)
    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")
    assert notes_data, "precondition: the doc has a note to persist"

    chat_id, _ = save_chat_atomically_row(db_path, None, "t", chat_data, notes_data, pins_data)

    # Reload EXACTLY as chat_library.loadChat does - from the DB, through the
    # notes table, not from the in-memory notes_data above.
    row = load_chat_row(db_path, chat_id)
    restored = SceneDocument()
    restore_chat_into_document(
        restored,
        row,
        load_notes_rows(db_path, chat_id),
        load_pins_rows(db_path, chat_id),
    )

    restored_notes = [n for n in restored.nodes.values() if n.kind == "note"]
    restored_chats = [n for n in restored.nodes.values() if n.kind == "chat"]
    assert len(restored_notes) == 1 and len(restored_chats) == 1
    assert _has_edge(restored, restored_notes[0].id, restored_chats[0].id), (
        "the system-prompt note lost its connection to the chat across a DB round-trip"
    )


def test_an_unreadable_asset_ref_is_preserved_rather_than_erased(tmp_path):
    """Regression: a TRANSIENT asset-read failure used to become permanent
    picture loss on the very next save.

    AssetStore.get() returns None both for "never stored" and for any OSError
    on read (an antivirus lock on Windows is enough). The image node then
    restored with no asset id, so the next save saw no bytes, fell through to
    the inline branch and wrote image_bytes:"" - erasing the only pointer to
    an asset file that was still sitting on disk the whole time. The ref is
    now carried across the round-trip so the row keeps pointing at it."""
    store = AssetStore(tmp_path / "assets")
    doc = SceneDocument()
    parent = doc.add_chat_node(0, 0, "draw me a cat", is_user=True)
    doc.add_image_node(0, 120, PNG, "a cat", parent.id, mime_type="image/png")

    saved = build_chat_data(doc, asset_store=store)
    image_payload = next(
        n for n in saved["nodes"] if n.get("node_type") == "image"
    )
    original_ref = image_payload["asset_ref"]
    assert original_ref

    # Reload while the store cannot produce the bytes (the transient failure).
    class _UnreadableStore:
        def get(self, ref):
            return None

        def put(self, data):  # pragma: no cover - not reached in this test
            return content_ref(data)

    notes_data = saved.pop("notes_data")
    pins_data = saved.pop("pins_data")
    reloaded = SceneDocument()
    restore_chat_into_document(
        reloaded, {"data": saved}, notes_data, pins_data, asset_store=_UnreadableStore()
    )

    # Now save again, exactly as an autosave tick would.
    resaved = build_chat_data(reloaded, asset_store=store)
    resaved_image = next(n for n in resaved["nodes"] if n.get("node_type") == "image")

    assert resaved_image.get("asset_ref") == original_ref, (
        "a transient asset-read failure erased the image's asset_ref on the next save"
    )
    assert not resaved_image.get("image_bytes")
