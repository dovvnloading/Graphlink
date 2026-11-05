"""ADR-009 stages 9.4/9.5: the `.graphlink` archive and the asset store.

Stage 9.4's exit criterion is "export round-trips on a second machine with
zero secrets; import inert" - so the tests here are organized as those
three claims plus the hostile-input cases an import path has to survive,
rather than as a walk through the happy path.

"A second machine" is simulated the only way it meaningfully can be
in-process: export from one isolated asset store and import into a
DIFFERENT, empty one, then assert the assets actually arrived. An import
that silently relied on bytes already present locally would pass a
same-store test and fail on the machine that matters.
"""

from __future__ import annotations

import json
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.asset_store import AssetStore, content_ref
from backend.workspace_archive import (
    ArchiveError,
    FORMAT_VERSION,
    export_archive,
    import_archive,
    read_archive,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"fake image bytes" * 4
SECRET = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
HOME_PATH = r"C:\Users\ada\Documents\private.xlsx"


def _chat(title="Trip planning", *, data=None, notes=None, pins=None, tags=None, favorite=False, archived=False):
    return {
        "title": title,
        "data": data if data is not None else {"nodes": [{"content": "hello"}]},
        "notes": notes or [],
        "pins": pins or [],
        "tags": tags or [],
        "favorite": favorite,
        "archived": archived,
    }


# -- the asset store ---------------------------------------------------------


def test_putting_the_same_bytes_twice_stores_once_and_returns_one_ref(tmp_path):
    store = AssetStore(tmp_path / "assets")
    first = store.put(PNG)
    second = store.put(PNG)
    assert first == second == content_ref(PNG)
    assert store.get(first) == PNG


def test_concurrent_puts_of_the_same_image_do_not_collide(tmp_path, monkeypatch):
    """Two independent saves of one image must both commit successfully.

    A barrier inside os.replace keeps the first writer from completing
    until both have staged their data. With the old shared "<hash>.tmp"
    filename, one rename succeeds and the other raises FileNotFoundError;
    independent temporary names make both writes safe and idempotent.
    """
    from backend import asset_store as asset_store_module

    root = tmp_path / "assets"
    stores = [AssetStore(root), AssetStore(root)]
    at_replace = threading.Barrier(2)
    real_replace = asset_store_module.os.replace

    def synchronize_replaces(source, destination):
        at_replace.wait(timeout=10)
        return real_replace(source, destination)

    monkeypatch.setattr(asset_store_module.os, "replace", synchronize_replaces)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(store.put, PNG) for store in stores]
        refs = [future.result(timeout=15) for future in futures]

    ref = content_ref(PNG)
    assert refs == [ref, ref]
    assert stores[0].get(ref) == PNG
    assert [path.name for path in (root / ref[:2]).iterdir()] == [ref]


@pytest.mark.parametrize(
    ("peer_bytes", "should_succeed"),
    [(PNG, True), (b"wrong image bytes", False), (None, False)],
)
def test_windows_replace_denial_only_succeeds_for_identical_committed_asset(
    tmp_path, monkeypatch, peer_bytes, should_succeed,
):
    """Simulate the losing Windows writer after passing the is_file check.

    A second concurrent os.replace can fail with WinError 5 when another
    writer already installed the same digest. Only matching FINAL bytes
    justify treating that failure as a completed idempotent save; a
    different/missing target must still report the original error.
    """
    from backend import asset_store as asset_store_module

    store = AssetStore(tmp_path / "assets")
    ref = content_ref(PNG)
    target = tmp_path / "assets" / ref[:2] / ref

    def replaced_by_peer(source, destination):
        assert destination == target
        if peer_bytes is not None:
            target.write_bytes(peer_bytes)
        raise PermissionError("simulated WinError 5 during concurrent os.replace")

    monkeypatch.setattr(asset_store_module.os, "replace", replaced_by_peer)
    if should_succeed:
        assert store.put(PNG) == ref
        assert store.get(ref) == PNG
    else:
        with pytest.raises(PermissionError, match="WinError 5"):
            store.put(PNG)
        # A wrong-hash file may remain on disk after a denied replacement,
        # but the verified read must not surface its bytes as this ref.
        assert store.get(ref) is None
        if peer_bytes is None:
            assert not target.exists()
        else:
            assert target.read_bytes() == peer_bytes

    assert list(target.parent.glob("*.tmp")) == []


@pytest.mark.parametrize("invalid_ref", [None, 123, [], {}, True])
def test_asset_store_rejects_non_string_references_as_missing(tmp_path, invalid_ref):
    store = AssetStore(tmp_path / "assets")
    assert store.get(invalid_ref) is None
    assert store.exists(invalid_ref) is False
    assert store.verify(invalid_ref) is False


def test_a_ref_the_store_has_never_seen_reads_as_missing_not_an_error(tmp_path):
    # A missing picture must degrade to "no image", never to "this chat
    # will not load".
    store = AssetStore(tmp_path / "assets")
    assert store.get(content_ref(b"never stored")) is None


def test_verify_rejects_bytes_that_no_longer_match_their_own_name(tmp_path):
    store = AssetStore(tmp_path / "assets")
    ref = store.put(PNG)
    assert store.verify(ref) is True

    # Corrupt the stored file behind the store's back.
    (tmp_path / "assets" / ref[:2] / ref).write_bytes(b"tampered")
    assert store.verify(ref) is False


def test_get_refuses_corrupt_bytes_without_mutating_the_stored_file(tmp_path):
    store = AssetStore(tmp_path / "assets")
    ref = store.put(PNG)
    target = tmp_path / "assets" / ref[:2] / ref
    target.write_bytes(b"truncated or otherwise corrupted bytes")

    assert store.exists(ref), "the file still exists at its original hash path"
    assert store.get(ref) is None, "get must never deliver wrong bytes under a trusted hash"
    assert store.verify(ref) is False
    assert target.read_bytes() == b"truncated or otherwise corrupted bytes"

    # Once the correct image bytes become available again, the earlier
    # atomic-repair behavior must still restore the original content ref.
    assert store.put(PNG) == ref
    assert store.get(ref) == PNG
    assert store.verify(ref) is True


def test_put_repairs_an_existing_corrupted_asset(tmp_path):
    store = AssetStore(tmp_path / "assets")
    ref = store.put(PNG)
    target = tmp_path / "assets" / ref[:2] / ref
    target.write_bytes(b"corrupted after first save")

    assert store.verify(ref) is False
    assert store.put(PNG) == ref
    assert store.verify(ref) is True
    assert target.read_bytes() == PNG
    assert sorted(p.name for p in target.parent.iterdir()) == [ref]


def test_put_still_skips_rewriting_an_intact_asset(tmp_path, monkeypatch):
    from backend import asset_store as asset_store_module

    store = AssetStore(tmp_path / "assets")
    ref = store.put(PNG)

    def unexpected_replace(*_args):
        raise AssertionError("an intact content-addressed asset must not be rewritten")

    monkeypatch.setattr(asset_store_module.os, "replace", unexpected_replace)
    assert store.put(PNG) == ref


def test_failed_corrupt_asset_repair_raises_and_cleans_up(tmp_path, monkeypatch):
    from backend import asset_store as asset_store_module

    store = AssetStore(tmp_path / "assets")
    ref = store.put(PNG)
    target = tmp_path / "assets" / ref[:2] / ref
    corrupted = b"corrupted after first save"
    target.write_bytes(corrupted)
    calls = []

    def deny_replace(source, destination):
        calls.append((source, destination))
        raise PermissionError("simulated Windows rename denial")

    monkeypatch.setattr(asset_store_module.os, "replace", deny_replace)
    with pytest.raises(PermissionError, match="Windows rename denial"):
        store.put(PNG)

    assert len(calls) == 1, "corrupted file must reach the atomic repair path"
    assert target.read_bytes() == corrupted, "failed repair must not partially overwrite the existing file"
    assert not list(target.parent.glob("*.tmp")), "failed repair left temporary files behind"


def test_import_repairs_a_corrupt_asset_already_in_target_store(tmp_path):
    source_store = AssetStore(tmp_path / "source-assets")
    ref = source_store.put(PNG)
    archive_path = tmp_path / "images.graphlink"
    export_archive(
        archive_path,
        [_chat(data={"nodes": [{"node_type": "image", "asset_ref": ref, "mime_type": "image/png"}]})],
        live_assets=source_store,
    )

    target_store = AssetStore(tmp_path / "target-assets")
    assert target_store.put(PNG) == ref
    target_path = tmp_path / "target-assets" / ref[:2] / ref
    target_path.write_bytes(b"corrupted after import")
    assert target_store.verify(ref) is False

    imported = import_archive(archive_path, target_assets=target_store)
    assert len(imported) == 1
    assert target_store.verify(ref) is True
    assert target_store.get(ref) == PNG


# -- export: round-trip ------------------------------------------------------


def test_a_chat_round_trips_through_export_and_import(tmp_path):
    archive = tmp_path / "out.graphlink"
    export_archive(archive, [_chat(data={"nodes": [{"content": "keep me"}]})])

    chats = import_archive(archive)

    assert len(chats) == 1
    assert chats[0]["title"] == "Trip planning"
    assert chats[0]["data"]["nodes"][0]["content"] == "keep me"


# REVIEW-FIX: tags/favorite/archived used to be dropped entirely on export -
# export_archive's payload was hardcoded to {title, data, notes, pins}, with
# no field anywhere in the format for these three to travel through. See
# backend/chat_library.py's make_export_workspace._load_one for where the
# real export path now sources them from (get_all_chats, not load_chat_row/
# load_notes_rows/load_pins_rows).


def test_tags_favorite_and_archived_round_trip_through_export_and_import(tmp_path):
    archive = tmp_path / "out.graphlink"
    export_archive(archive, [_chat(tags=["work", "urgent"], favorite=True, archived=True)])

    chats = import_archive(archive)

    assert len(chats) == 1
    assert chats[0]["tags"] == ["work", "urgent"]
    assert chats[0]["favorite"] is True
    assert chats[0]["archived"] is True


def test_an_older_archive_without_tags_favorite_archived_defaults_them_back_rather_than_crashing(tmp_path):
    # Simulates an archive written before this fix existed: its chat entry
    # is exactly the old {title, data, notes, pins} shape, with no tags/
    # favorite/archived keys at all - import_archive must default them
    # rather than KeyError or crash.
    archive = tmp_path / "pre_fix.graphlink"
    old_shaped_chat = {"title": "Old Export", "data": {"nodes": []}, "notes": [], "pins": []}
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"formatVersion": FORMAT_VERSION}))
        zf.writestr("chats/0.json", json.dumps(old_shaped_chat))

    chats = import_archive(archive)

    assert len(chats) == 1
    assert chats[0]["title"] == "Old Export"
    assert chats[0]["tags"] == []
    assert chats[0]["favorite"] is False
    assert chats[0]["archived"] is False


def test_export_and_import_preserve_twelve_graphs_in_index_order(tmp_path):
    archive = tmp_path / "twelve.graphlink"
    expected = [f"Graph {i}" for i in range(12)]
    export_archive(archive, [_chat(title=name) for name in expected])
    assert [chat["title"] for chat in import_archive(archive)] == expected


def test_older_archive_without_manifest_index_uses_numeric_order(tmp_path):
    archive = tmp_path / "old-twelve.graphlink"
    expected = [f"Graph {i}" for i in range(12)]
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"formatVersion": FORMAT_VERSION}))
        for i, name in enumerate(expected):
            zf.writestr(f"chats/{i}.json", json.dumps(_chat(title=name)))
    assert [chat["title"] for chat in import_archive(archive)] == expected


@pytest.mark.parametrize("manifest_fields", [
    {"chats": "invalid"},
    {"chats": [{"member": "chats/1.json"}]},
    {"chats": [{"member": "chats/0.json"}, {"member": "chats/0.json"}]},
    {"chatCount": 2},
    {"chatCount": True},
])
def test_archive_rejects_inconsistent_manifest_indexes_and_counts(tmp_path, manifest_fields):
    archive = tmp_path / "bad-index.graphlink"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "formatVersion": FORMAT_VERSION, **manifest_fields,
        }))
        zf.writestr("chats/0.json", json.dumps(_chat()))
    with pytest.raises(ArchiveError, match="(index|count)"):
        read_archive(archive)


def test_assets_round_trip_into_a_DIFFERENT_store_the_second_machine_case(tmp_path):
    source_store = AssetStore(tmp_path / "source-assets")
    ref = source_store.put(PNG)
    data = {"nodes": [{"node_type": "image", "asset_ref": ref, "mime_type": "image/png"}]}

    archive = tmp_path / "out.graphlink"
    export_archive(archive, [_chat(data=data)], live_assets=source_store)

    # A completely empty store, standing in for the second machine.
    target_store = AssetStore(tmp_path / "target-assets")
    assert target_store.get(ref) is None

    import_archive(archive, target_assets=target_store)

    assert target_store.get(ref) == PNG, "the image did not survive the trip"


def test_assets_are_carried_as_real_files_not_base64_in_the_json(tmp_path):
    # The whole point of the format: unzip it and your pictures are there.
    store = AssetStore(tmp_path / "assets")
    ref = store.put(PNG)
    archive = tmp_path / "out.graphlink"
    export_archive(
        archive,
        [_chat(data={"nodes": [{"asset_ref": ref, "mime_type": "image/png"}]})],
        live_assets=store,
    )

    with zipfile.ZipFile(archive) as zf:
        asset_members = [n for n in zf.namelist() if n.startswith("assets/")]
        assert asset_members == [f"assets/{ref}.png"]
        assert zf.read(asset_members[0]) == PNG


def test_export_skips_a_corrupt_live_asset_and_recovers_after_repair(tmp_path):
    store = AssetStore(tmp_path / "assets")
    ref = store.put(PNG)
    path = tmp_path / "assets" / ref[:2] / ref
    path.write_bytes(b"corrupted on disk")
    chat = _chat(data={"nodes": [
        {"node_type": "image", "asset_ref": ref, "mime_type": "image/png"},
    ]})

    bad_archive = tmp_path / "corrupt-source.graphlink"
    export_archive(bad_archive, [chat], live_assets=store)

    with zipfile.ZipFile(bad_archive) as zf:
        assert not [name for name in zf.namelist() if name.startswith("assets/")]
        assert json.loads(zf.read("manifest.json"))["assetCount"] == 0
        assert json.loads(zf.read("chats/0.json"))["data"]["nodes"][0]["asset_ref"] == ref

    # The export must not silently bless damaged bytes, or rewrite the
    # original graph's reference to a new digest for those damaged bytes.
    assert store.get(ref) is None
    assert chat["data"]["nodes"][0]["asset_ref"] == ref

    store.put(PNG)
    repaired_archive = tmp_path / "repaired-source.graphlink"
    export_archive(repaired_archive, [chat], live_assets=store)
    with zipfile.ZipFile(repaired_archive) as zf:
        assert zf.read(f"assets/{ref}.png") == PNG
        assert json.loads(zf.read("manifest.json"))["assetCount"] == 1


def test_an_asset_missing_from_the_store_does_not_abort_the_whole_export(tmp_path):
    # One lost picture must not cost the user every conversation.
    store = AssetStore(tmp_path / "assets")
    archive = tmp_path / "out.graphlink"

    export_archive(
        archive,
        [_chat(data={"nodes": [{"asset_ref": content_ref(b"gone"), "mime_type": "image/png"}]})],
        live_assets=store,
    )

    assert len(import_archive(archive)) == 1


# -- export: zero secrets ----------------------------------------------------


def test_no_secret_or_local_path_survives_into_the_archive(tmp_path):
    archive = tmp_path / "out.graphlink"
    export_archive(
        archive,
        [
            _chat(
                data={"nodes": [{"content": f"my key is {SECRET}"}, {"error": f"cannot read {HOME_PATH}"}]},
                notes=[{"text": SECRET}],
            )
        ],
    )

    raw = archive.read_bytes().decode("utf-8", errors="replace")
    # The zip is deflated, so also check the parsed form - the raw check
    # alone could pass simply because the bytes were compressed.
    parsed = json.dumps(import_archive(archive))
    for haystack in (raw, parsed):
        assert SECRET not in haystack
        assert HOME_PATH not in haystack
    assert "ada" not in parsed, "leaked the operator's account name"


@pytest.mark.parametrize("sensitive_value", [SECRET, HOME_PATH])
def test_export_scrubs_sensitive_chat_titles_in_payload_and_manifest(tmp_path, sensitive_value):
    """A title is exported twice (chat JSON and manifest index); neither may leak."""
    archive = tmp_path / "sensitive-title.graphlink"
    title = f"Debugging {sensitive_value} in production"
    chat = _chat(title=title)

    export_archive(archive, [chat])

    # Inspect actual members, not just import_archive's parsed chats. The
    # manifest is shareable and visible even without opening a chat file.
    with zipfile.ZipFile(archive) as zf:
        exported = json.loads(zf.read("chats/0.json"))
        manifest = json.loads(zf.read("manifest.json"))
        combined = zf.read("chats/0.json") + zf.read("manifest.json")

    assert sensitive_value.encode("utf-8") not in combined
    assert exported["title"] != title
    assert exported["title"].startswith("Debugging ")
    assert exported["title"].endswith(" in production")
    assert manifest["chats"][0]["title"] == exported["title"]
    assert import_archive(archive)[0]["title"] == exported["title"]
    assert chat["title"] == title, "export must not modify the original conversation"


def test_export_keeps_plain_and_empty_chat_titles_unchanged(tmp_path):
    archive = tmp_path / "plain-titles.graphlink"
    originals = [_chat(title="Quarterly planning"), _chat(title="")]
    export_archive(archive, originals)

    with zipfile.ZipFile(archive) as zf:
        exported_titles = [json.loads(zf.read(f"chats/{i}.json"))["title"] for i in range(2)]
        manifest = json.loads(zf.read("manifest.json"))

    assert exported_titles == ["Quarterly planning", "Untitled"]
    assert [entry["title"] for entry in manifest["chats"]] == exported_titles
    assert originals[0]["title"] == "Quarterly planning"
    assert originals[1]["title"] == ""


def test_export_scrubs_url_query_credentials_in_title_content_and_manifest(tmp_path):
    """Inspect the actual ZIP members; importing alone does not read manifest titles."""
    archive = tmp_path / "redacted-url.graphlink"
    credential = "private-test-value-8192"  # not an issuer-shaped credential
    url = f"https://service.example.test/items?api%5fkey={credential}&limit=10"
    chat = _chat(
        title=f"Investigate {url}",
        data={"nodes": [{"content": f"Evidence: {url}"}]},
        notes=[{"text": f"Source: {url}"}],
    )

    export_archive(archive, [chat])

    with zipfile.ZipFile(archive) as zf:
        chat_json = zf.read("chats/0.json")
        manifest_json = zf.read("manifest.json")
        exported = json.loads(chat_json)
        manifest = json.loads(manifest_json)

    expected_url = "https://service.example.test/items?api%5fkey=[redacted]&limit=10"
    assert credential.encode("utf-8") not in chat_json + manifest_json
    assert exported["title"] == f"Investigate {expected_url}"
    assert exported["data"]["nodes"][0]["content"] == f"Evidence: {expected_url}"
    assert exported["notes"][0]["text"] == f"Source: {expected_url}"
    assert manifest["chats"][0]["title"] == exported["title"]
    assert chat["title"] == f"Investigate {url}", "export mutated the original chat"


# -- import: hostile input ---------------------------------------------------


def test_a_zip_slip_entry_is_refused(tmp_path):
    # The classic: a member name that escapes the extraction root. Python's
    # zipfile will happily hand you this path; refusing it is on us.
    archive = tmp_path / "evil.graphlink"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"formatVersion": FORMAT_VERSION}))
        zf.writestr("../../escaped.txt", "pwned")

    with pytest.raises(ArchiveError, match="escapes the archive"):
        read_archive(archive)


def test_an_absolute_path_entry_is_refused(tmp_path):
    archive = tmp_path / "evil.graphlink"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"formatVersion": FORMAT_VERSION}))
        zf.writestr("C:/Windows/System32/drivers/etc/hosts", "pwned")

    with pytest.raises(ArchiveError, match="absolute path"):
        read_archive(archive)


def test_an_archive_whose_members_sum_past_the_aggregate_cap_is_refused(tmp_path, monkeypatch):
    # SECURITY-FIX regression: MAX_MEMBER_BYTES alone bounds ONE entry's
    # declared size, but nothing bounded how many members an archive could
    # carry or what their sizes summed to - a sub-MB archive with enough
    # members could balloon to gigabytes in RAM without any single member
    # ever tripping the per-member cap. Monkeypatches the real (2GB)
    # threshold down to something a test can actually exceed without
    # writing gigabytes of real data.
    import backend.workspace_archive as workspace_archive_module
    monkeypatch.setattr(workspace_archive_module, "MAX_TOTAL_UNCOMPRESSED_BYTES", 100)

    archive = tmp_path / "bomb.graphlink"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"formatVersion": FORMAT_VERSION}))
        for i in range(20):
            zf.writestr(f"chats/{i}.json", json.dumps(_chat(title=f"chat {i}")))

    with pytest.raises(ArchiveError, match="total more than"):
        read_archive(archive)


@pytest.mark.parametrize("bad_manifest", [[], None, 3, "text", True])
def test_archive_rejects_non_object_manifest_with_archive_error(tmp_path, bad_manifest):
    archive = tmp_path / "bad-manifest.graphlink"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps(bad_manifest))
    with pytest.raises(ArchiveError, match="invalid manifest"):
        read_archive(archive)


@pytest.mark.parametrize("version", [True, False, 0, -1, 1.0])
def test_archive_rejects_non_positive_or_non_integer_versions(tmp_path, version):
    archive = tmp_path / "bad-version.graphlink"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"formatVersion": version}))
    with pytest.raises(ArchiveError, match="usable format version"):
        read_archive(archive)


@pytest.mark.parametrize("bad_chat", [None, [], "oops", 17, {"title": "Incomplete"},
    {"title": "T", "data": [], "notes": []},
    {"title": "T", "data": {}, "notes": "not-a-list"},
    {"title": "T", "data": {}, "favorite": "false"}])
def test_invalid_chat_is_refused_before_any_asset_is_written(tmp_path, bad_chat):
    good_ref = content_ref(PNG)
    archive = tmp_path / "bad-chat.graphlink"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"formatVersion": FORMAT_VERSION}))
        zf.writestr("chats/0.json", json.dumps(bad_chat))
        zf.writestr(f"assets/{good_ref}.png", PNG)
    store = AssetStore(tmp_path / "assets")
    with pytest.raises(ArchiveError, match="invalid chat entry"):
        import_archive(archive, target_assets=store)
    assert store.get(good_ref) is None


def test_duplicate_zip_member_names_are_rejected(tmp_path):
    import warnings

    archive = tmp_path / "duplicate.graphlink"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"formatVersion": FORMAT_VERSION}))
            zf.writestr("manifest.json", json.dumps({"formatVersion": FORMAT_VERSION}))
    with pytest.raises(ArchiveError, match="duplicate archive entries"):
        read_archive(archive)


def test_backslash_members_are_rejected_with_archive_error(tmp_path):
    import os

    from backend.workspace_archive import _safe_member_name

    # The validation helper refuses a literal backslash on EVERY platform.
    with pytest.raises(ArchiveError, match="unsupported path separators"):
        _safe_member_name(r"chats\0.json")

    # On Windows, zipfile.ZipInfo normalizes os.sep to "/" even while
    # READING a raw ZIP with literal backslash bytes in its central
    # directory. The archive reader never sees the original backslash;
    # its normalized name is already safe. Exercise the archive path on
    # POSIX, where zipfile preserves the hostile member name unchanged.
    if os.name == "nt":
        return
    archive = tmp_path / "backslash.graphlink"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"formatVersion": FORMAT_VERSION}))
        zf.writestr(r"chats\0.json", json.dumps(_chat()))
    with pytest.raises(ArchiveError, match="unsupported path separators"):
        read_archive(archive)


def test_an_archive_within_the_aggregate_cap_is_unaffected(tmp_path, monkeypatch):
    import backend.workspace_archive as workspace_archive_module
    monkeypatch.setattr(workspace_archive_module, "MAX_TOTAL_UNCOMPRESSED_BYTES", 1_000_000)

    archive = tmp_path / "normal.graphlink"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"formatVersion": FORMAT_VERSION}))
        zf.writestr("chats/0.json", json.dumps(_chat()))

    result = read_archive(archive)  # must not raise
    assert len(result["chats"]) == 1


def test_an_archive_from_a_newer_format_version_is_refused_not_guessed_at(tmp_path):
    archive = tmp_path / "future.graphlink"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"formatVersion": FORMAT_VERSION + 1}))

    with pytest.raises(ArchiveError, match="newer version"):
        read_archive(archive)


def test_a_file_that_is_not_a_zip_at_all_is_refused_cleanly(tmp_path):
    archive = tmp_path / "notazip.graphlink"
    archive.write_bytes(b"this is just some text")

    with pytest.raises(ArchiveError, match="not a readable archive"):
        read_archive(archive)


def test_a_zip_without_a_manifest_is_refused(tmp_path):
    archive = tmp_path / "bare.graphlink"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("chats/0.json", json.dumps(_chat()))

    with pytest.raises(ArchiveError, match="not a Graphlink archive"):
        read_archive(archive)


def test_an_asset_whose_bytes_do_not_match_its_ref_is_dropped_not_stored(tmp_path):
    # Content addressing is only worth anything if the name is checked
    # against the bytes on the way in.
    archive = tmp_path / "tampered.graphlink"
    honest_ref = content_ref(PNG)
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"formatVersion": FORMAT_VERSION}))
        zf.writestr("chats/0.json", json.dumps(_chat()))
        zf.writestr(f"assets/{honest_ref}.png", b"NOT the bytes this ref names")

    store = AssetStore(tmp_path / "assets")
    import_archive(archive, target_assets=store)

    assert store.get(honest_ref) is None, "stored bytes under a ref that lies about them"


def test_reading_an_archive_writes_nothing_anywhere(tmp_path):
    # "Import is inert" starts with validation being side-effect free.
    archive = tmp_path / "out.graphlink"
    export_archive(archive, [_chat()])
    before = sorted(p.name for p in tmp_path.iterdir())

    read_archive(archive)

    assert sorted(p.name for p in tmp_path.iterdir()) == before


# -- export atomicity --------------------------------------------------------


def test_parallel_exports_use_independent_staging_files(tmp_path, monkeypatch):
    # Two export workers can finish their ZIP writes together. A shared
    # .tmp path causes the second rename to fail or publishes partial data.
    from backend import workspace_archive as archive_module

    archive = tmp_path / "parallel.graphlink"
    barrier = threading.Barrier(2)
    real_replace = archive_module.os.replace
    calls_lock = threading.Lock()
    first_arrivals = 0

    def synchronized_replace(source, destination):
        nonlocal first_arrivals
        # Synchronize the FIRST two writers only: a transient Windows
        # sharing-denial retry is a third call and must not wait for a new
        # partner at this barrier after both initial writers have passed.
        with calls_lock:
            first_arrivals += 1
            first = first_arrivals <= 2
        if first:
            barrier.wait(timeout=10)
        return real_replace(source, destination)

    monkeypatch.setattr(archive_module.os, "replace", synchronized_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(export_archive, archive, [_chat(title="first")]),
            pool.submit(export_archive, archive, [_chat(title="second")]),
        ]
        assert [future.result(timeout=15) for future in futures] == [archive, archive]

    assert import_archive(archive)[0]["title"] in {"first", "second"}
    assert not list(tmp_path.glob("*.tmp"))


def test_export_retries_transient_windows_sharing_denial_without_losing_data(tmp_path, monkeypatch):
    from backend import workspace_archive as archive_module

    original = archive_module.os.replace
    attempts = []

    def fail_twice(source, target):
        attempts.append((source, target))
        if len(attempts) <= 2:
            raise PermissionError("simulated Windows sharing violation")
        return original(source, target)

    monkeypatch.setattr(archive_module, "_is_transient_windows_replace_error", lambda exc: True)
    monkeypatch.setattr(archive_module, "_WINDOWS_REPLACE_BACKOFF_SECONDS", (0.0, 0.0))
    monkeypatch.setattr(archive_module.os, "replace", fail_twice)
    target = tmp_path / "out.graphlink"

    assert export_archive(target, [_chat(title="correct archive")]) == target
    assert len(attempts) == 3
    assert import_archive(target)[0]["title"] == "correct archive"
    assert not list(tmp_path.glob("*.tmp"))


def test_export_does_not_swallow_permanent_windows_rename_denial(tmp_path, monkeypatch):
    from backend import workspace_archive as archive_module

    target = tmp_path / "out.graphlink"
    original = b"preexisting content"
    target.write_bytes(original)

    def deny_replace(source, destination):
        raise PermissionError("permanent permission denial")

    monkeypatch.setattr(archive_module, "_is_transient_windows_replace_error", lambda exc: False)
    monkeypatch.setattr(archive_module.os, "replace", deny_replace)
    with pytest.raises(PermissionError, match="permanent permission"):
        export_archive(target, [_chat()])
    assert target.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_a_failed_export_leaves_no_file_wearing_the_real_name(tmp_path):
    archive = tmp_path / "out.graphlink"

    class Exploding(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("simulated failure mid-export")

    with pytest.raises(RuntimeError):
        export_archive(archive, [Exploding()])

    assert not archive.exists(), "a partial export must never wear the final name"
    assert not (tmp_path / "out.graphlink.tmp").exists(), "temp file left behind"
