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
import zipfile
from pathlib import Path

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


def _chat(title="Trip planning", *, data=None, notes=None, pins=None):
    return {
        "title": title,
        "data": data if data is not None else {"nodes": [{"content": "hello"}]},
        "notes": notes or [],
        "pins": pins or [],
    }


# -- the asset store ---------------------------------------------------------


def test_putting_the_same_bytes_twice_stores_once_and_returns_one_ref(tmp_path):
    store = AssetStore(tmp_path / "assets")
    first = store.put(PNG)
    second = store.put(PNG)
    assert first == second == content_ref(PNG)
    assert store.get(first) == PNG


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


# -- export: round-trip ------------------------------------------------------


def test_a_chat_round_trips_through_export_and_import(tmp_path):
    archive = tmp_path / "out.graphlink"
    export_archive(archive, [_chat(data={"nodes": [{"content": "keep me"}]})])

    chats = import_archive(archive)

    assert len(chats) == 1
    assert chats[0]["title"] == "Trip planning"
    assert chats[0]["data"]["nodes"][0]["content"] == "keep me"


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


def test_a_failed_export_leaves_no_file_wearing_the_real_name(tmp_path):
    archive = tmp_path / "out.graphlink"

    class Exploding(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("simulated failure mid-export")

    with pytest.raises(RuntimeError):
        export_archive(archive, [Exploding()])

    assert not archive.exists(), "a partial export must never wear the final name"
    assert not (tmp_path / "out.graphlink.tmp").exists(), "temp file left behind"
