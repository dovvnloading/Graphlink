"""Scientific Workbench guide inventory must track the registered catalog.

The inventory is checked against the actual pack operations (including the
inferential operations registered as 'fitting'), not a second hard-coded list.
"""

from pathlib import Path
import re

from graphlink_plugins.scientific.catalog import PACKS, operations


GUIDE = Path(__file__).resolve().parents[2] / "docs" / "SCIENTIFIC_WORKBENCH.md"
ROW = re.compile(
    r"^\| `(?P<pack>[a-z]+)\.(?P<operation>[a-z][a-z0-9_]*)` — (?P<title>.+?) \|",
    re.MULTILINE,
)


def test_scientific_workbench_guide_covers_every_operation_and_field():
    guide = GUIDE.read_text(encoding="utf-8")
    documented = {}
    for line in guide.splitlines():
        match = ROW.match(line)
        if not match:
            continue
        name = (match["pack"], match["operation"])
        assert name not in documented, f"Duplicate documented operation: {name}"
        # In Markdown tables, declared unit strings can contain escaped pipes.
        columns = re.split(r"(?<!\\)\|", line)
        assert len(columns) == 5, f"Malformed operation row: {name}"
        documented[name] = {
            "title": match["title"],
            "inputs": columns[2].strip(),
            "outputs": columns[3].strip(),
        }

    catalog = {(pack, op.id): op for pack in PACKS for op in operations(pack)}
    assert set(documented) == set(catalog), (
        f"Undocumented operations: {sorted(set(catalog) - set(documented))}; "
        f"unknown documented operations: {sorted(set(documented) - set(catalog))}"
    )
    for name, operation in catalog.items():
        documented_row = documented[name]
        assert documented_row["title"] == operation.title, f"Stale title: {name}"
        assert documented_row["outputs"], f"Missing output description: {name}"
        for field in operation.fields:
            unit = (field.unit or "—").replace("|", r"\|")
            signature = f"`{field.key}` ({field.kind}; `{unit}`)"
            assert signature in documented_row["inputs"], (
                f"Missing or stale input/units in {name}: {signature}"
            )


def test_scientific_workbench_guide_links_audit_and_pack_references():
    guide = GUIDE.read_text(encoding="utf-8")
    assert "(SCIENTIFIC_CORRECTNESS_AUDIT.md)" in guide
    # Each registered pack has an operation heading with its model reference.
    for pack, (title, _) in PACKS.items():
        heading = f"### {title} (`{pack}`)"
        assert heading in guide, f"Missing pack section: {pack}"
        section = guide.split(heading, 1)[1].split("\n### ", 1)[0]
        assert "](SCIENTIFIC_" in section and "_REFERENCE.md)" in section, (
            f"Missing pack reference: {pack}"
        )
