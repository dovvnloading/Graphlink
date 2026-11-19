"""Content-addressed plugin state through save, reload, archive and revoked grants."""

import json

import pytest

from backend.asset_store import AssetStore, content_ref
from backend.canvas import SceneDocument
from backend.domain.node_states import PersistedPluginState
from backend.plugin_sdk import HostContext, PluginNodeSeed, PluginRegistry
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data
from backend.workspace_archive import _collect_asset_refs, export_archive, import_archive
from graphlink_settings_store import SettingsManager


KIND = "sample.result"
LARGE = {"version": 1, "latest": {"series": [1.123456789] * 1000}}


@pytest.fixture
def registered():
    calls = []
    host = HostContext("sample")

    def serialize_saved(node):
        calls.append("save")
        return node.state.data

    def deserialize(data):
        calls.append("load")
        return PersistedPluginState(data=data)

    host.register_node_kind(
        "result",
        lambda *_: PluginNodeSeed(title="Result"),
        requires_parent=False,
        serialize=lambda _: {"catalog": "wire only"},
        serialize_saved=serialize_saved,
        deserialize=deserialize,
        externalize_state=True,
    )
    registry = PluginRegistry()
    registry.node_kinds.update(host._node_kinds)
    return registry, calls


def _document(data):
    document = SceneDocument()
    document.add_plugin_node(KIND, 0, 0, None, title="Measurement", state=PersistedPluginState(data=data))
    return document


def _restore(data, registry, store=None, settings=None):
    document = SceneDocument()
    restore_chat_into_document(
        document, {"data": data}, [], [], asset_store=store, plugin_registry=registry, settings_manager=settings
    )
    return document


def _save(document, registry, store=None, settings=None):
    return build_chat_data(document, asset_store=store, plugin_registry=registry, settings_manager=settings)


def test_small_or_storeless_state_is_compact_inline_json(tmp_path, registered):
    registry, calls = registered
    store = AssetStore(tmp_path / "assets")
    data = {"version": 1, "latest": {"answer": 42}}
    inline = _save(_document(data), registry, store)["nodes"][0]
    assert inline["plugin_state"] == data
    assert "plugin_state_asset" not in inline and "catalog" not in json.dumps(inline)
    storeless = _save(_document(LARGE), registry)
    assert storeless["nodes"][0]["plugin_state"] == LARGE
    assert next(iter(_restore(storeless, registry).nodes.values())).state.data == LARGE
    assert calls == ["save", "save", "load"]


def test_large_state_deduplicates_and_survives_archive_gc_and_restore(tmp_path, registered):
    registry, _ = registered
    source = AssetStore(tmp_path / "assets")
    document = _document(LARGE)
    document.add_plugin_node(KIND, 2, 3, None, title="Copy", state=PersistedPluginState(data=LARGE))
    saved = _save(document, registry, source)
    first, second = saved["nodes"]
    assert "plugin_state" not in first
    descriptor = first["plugin_state_asset"]
    assert descriptor == second["plugin_state_asset"] and descriptor["mime_type"] == "application/json"
    ref = descriptor["asset_ref"]
    assert json.loads(source.get(ref)) == LARGE
    assert len(list(source.root.glob("*/*"))) == 1
    refs = set()
    _collect_asset_refs(saved, refs)  # Used by both workspace export and asset GC.
    assert refs == {ref}

    archive = export_archive(
        tmp_path / "measurements.graphlink", [{"title": "Results", "data": saved}], live_assets=source
    )
    target = AssetStore(tmp_path / "imported")
    imported = import_archive(archive, target_assets=target)
    assert target.verify(ref)
    restored = _restore(imported[0]["data"], registry, target)
    assert all(node.state.data == LARGE for node in restored.nodes.values())


@pytest.mark.parametrize("loss", ["missing", "corrupt", "storeless", "invalid-json"])
def test_unresolved_reference_is_preserved_and_can_be_recovered(tmp_path, registered, loss):
    registry, calls = registered
    store = AssetStore(tmp_path / "assets")
    saved = _save(_document(LARGE), registry, store)
    descriptor = saved["nodes"][0]["plugin_state_asset"]
    ref = descriptor["asset_ref"]
    original = store.get(ref)
    path = store.root / ref[:2] / ref
    if loss == "missing":
        path.unlink()
    if loss == "corrupt":
        path.write_bytes(b"damaged")
    if loss == "invalid-json":
        descriptor["asset_ref"] = store.put(b"wrong format")
    document = _restore(saved, registry, None if loss == "storeless" else store)
    assert calls == ["save"]
    node = next(iter(document.nodes.values()))
    assert node.title == "Measurement" and node.state.unresolved_asset_ref == descriptor["asset_ref"]
    resaved = _save(document, registry, store)
    assert resaved["nodes"][0]["plugin_state_asset"] == descriptor
    assert calls == ["save"]
    if loss != "invalid-json":
        assert store.put(original) == ref
        recovered = next(iter(_restore(resaved, registry, store).nodes.values()))
        assert recovered.state.data == LARGE and calls[-1] == "load"


def test_revocation_before_first_save_keeps_latest_without_calling_plugin(tmp_path, registered):
    registry, calls = registered
    store = AssetStore(tmp_path / "assets")
    settings = SettingsManager(tmp_path / "session.dat")
    document = _document(LARGE)
    saved = _save(document, registry, store, settings)
    descriptor = saved["nodes"][0]["plugin_state_asset"]
    ref = descriptor["asset_ref"]
    assert json.loads(store.get(ref)) == LARGE and not calls

    restored = _restore(saved, registry, store, settings)
    # Read verified bytes as passive JSON so a later regrant works immediately.
    assert next(iter(restored.nodes.values())).state.data == LARGE
    resaved = _save(restored, registry, store, settings)
    assert resaved["nodes"][0]["plugin_state_asset"] == descriptor and not calls
    settings.set_plugin_grant("sample", True)
    assert next(iter(_restore(resaved, registry, store, settings).nodes.values())).state.data == LARGE
    assert calls == ["load"]


def test_revoked_inline_result_survives_backwards_compatible_save(tmp_path, registered):
    registry, calls = registered
    settings = SettingsManager(tmp_path / "session.dat")
    initial = {"nodes": [{"node_type": KIND, "plugin_state": {"version": 1, "n": 4}}]}
    saved = _save(_restore(initial, registry, settings=settings), registry, settings=settings)
    assert saved["nodes"][0]["plugin_state"] == {"version": 1, "n": 4}
    assert not calls


def test_storage_failure_keeps_live_state_and_allows_retry(tmp_path, registered, monkeypatch):
    registry, _ = registered
    store = AssetStore(tmp_path / "assets")
    document = _document(LARGE)
    with monkeypatch.context() as patch:

        def fail(_data):
            raise OSError("Disk full")

        patch.setattr(store, "put", fail)
        with pytest.raises(OSError, match="Disk full"):
            _save(document, registry, store)
    descriptor = _save(document, registry, store)["nodes"][0]["plugin_state_asset"]
    assert content_ref(store.get(descriptor["asset_ref"])) == descriptor["asset_ref"]


def test_archive_follows_nested_typed_table_asset_in_externalized_plugin_state(tmp_path):
    """A wide scientific table's metadata may itself exceed the 8 KiB inline limit."""
    source = AssetStore(tmp_path / "assets")
    table_ref = source.put(b'{"version":1,"columns":[],"rows":[]}')
    state = {"latest": {"tableAsset": {
        "asset_ref": table_ref, "mime_type": "application/json",
    }}}
    state_ref = source.put(json.dumps(state).encode("utf-8"))
    saved = {"nodes": [{"node_type": KIND, "plugin_state_asset": {
        "asset_ref": state_ref, "mime_type": "application/json",
    }}]}
    # The ordinary walk can only see the outer descriptor, but export must
    # follow the verified plugin-state JSON to include the actual table.
    refs = set()
    _collect_asset_refs(saved, refs)
    assert refs == {state_ref}

    archive = export_archive(
        tmp_path / "nested.graphlink", [{"title": "Table", "data": saved}], live_assets=source,
    )
    target = AssetStore(tmp_path / "imported")
    imported = import_archive(archive, target_assets=target)
    assert imported[0]["data"] == saved
    assert target.verify(state_ref) and target.verify(table_ref)
