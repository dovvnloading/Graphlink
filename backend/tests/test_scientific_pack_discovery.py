"""A bundled Scientific Workbench pack can be added without registry edits."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.plugin_sdk import discover_plugins
from graphlink_plugins.scientific import catalog
from graphlink_plugins.scientific.packs import biology


def _write_new_pack(tmp_path: Path, family: str) -> tuple[Path, Path]:
    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "science_population_genetics"
    plugin_dir.mkdir(parents=True)
    pack_root = tmp_path / "packs"
    pack_root.mkdir()
    # Catalog discovery must not import this module; operations are loaded on grant.
    (pack_root / "population_genetics.py").write_text(
        "raise AssertionError('unapproved pack code was imported')\n", encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        "from graphlink_plugins.scientific.workbench import register_pack\n"
        "def register(host):\n"
        "    register_pack(host, host.plugin_id.removeprefix('science_'))\n",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.toml").write_text(
        '[plugin]\n'
        'id = "science_population_genetics"\n'
        'name = "Population Genetics"\n'
        'description = "Analyze population data."\n'
        'version = "1.1.0"\n'
        'sdk_api_version = 1\n'
        'entry_point = "plugin:register"\n\n'
        '[scopes]\ngrants = ["graph.read", "graph.mutate"]\n\n'
        '[frontend]\nview = "generic"\n\n'
        '[persistence]\npassive_node_kinds = ["workbench"]\n\n'
        f'[scientific]\nfamily = "{family}"\n',
        encoding="utf-8",
    )
    return plugins_root, pack_root


@pytest.mark.parametrize("family,category", [
    ("biology", "Biology Workbench"),
    ("", "Scientific Workbench"),
])
def test_new_manifest_and_module_register_without_catalog_edit(tmp_path, monkeypatch, family, category):
    plugins_root, pack_root = _write_new_pack(tmp_path, family)
    discovered = catalog.discover_pack_manifests(plugins_root, pack_root)
    assert discovered["population_genetics"] == (
        "Population Genetics", "Analyze population data.", family,
    )

    # Model the state following startup discovery. Only the pack's new files
    # would exist in a real installed checkout; no core source is edited.
    monkeypatch.setitem(catalog.PACKS, "population_genetics", discovered["population_genetics"][:2])
    if family:
        monkeypatch.setitem(catalog.PACK_FAMILIES, "population_genetics", family)
    original_import = catalog.import_module

    def import_module(module, package):
        if module == ".packs.population_genetics":
            return SimpleNamespace(OPERATIONS=(biology.OPERATIONS[0],))
        return original_import(module, package)

    monkeypatch.setattr(catalog, "import_module", import_module)
    registry = discover_plugins(plugins_root, load_unapproved_for_tests=True)
    assert not registry.load_errors
    assert registry.picker_entries["Population Genetics"].category == category
    assert registry.picker_entries["Population Genetics"].plugin_id == "science_population_genetics"
    assert "science_population_genetics.workbench" in registry.node_kinds
    assert len(registry.intents) > 0


def test_new_pack_requires_matching_manifest_module_and_plugin_entry(tmp_path):
    plugins_root, pack_root = _write_new_pack(tmp_path, "biology")
    manifest = plugins_root / "science_population_genetics" / "plugin.toml"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(text.replace('id = "science_population_genetics"', 'id = "science_other"'), encoding="utf-8")
    assert catalog.discover_pack_manifests(plugins_root, pack_root) == {}

    manifest.write_text(text, encoding="utf-8")
    (pack_root / "population_genetics.py").unlink()
    assert catalog.discover_pack_manifests(plugins_root, pack_root) == {}


def test_every_biology_pack_has_its_own_reference_page():
    docs = Path(__file__).resolve().parents[2] / "docs" / "biology"
    biology_packs = [pack for pack, family in catalog.PACK_FAMILIES.items() if family == "biology"]
    assert "biology" in biology_packs
    for pack in biology_packs:
        page = docs / f"{pack}.md"
        assert page.is_file(), f"Missing biology pack reference: {page}"
        assert f"science_{pack}" in page.read_text(encoding="utf-8")
