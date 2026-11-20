"""The historical guide inventory must track the original workbench packs.

Additional file-only packs have independent references, so their registration
does not require modifying the fixed guide table.
"""

from pathlib import Path
import re

from graphlink_plugins.scientific.catalog import PACKS, PINNED_PACKS, operations


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

    catalog = {(pack, op.id): op for pack in PINNED_PACKS for op in operations(pack)}
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


def test_biology_guide_discloses_bounded_discussion_context():
    guide = (GUIDE.parent / "BIOLOGY_WORKBENCH.md").read_text(encoding="utf-8")
    discussion = guide.split("- **Discuss in chat.**", 1)[1].split("\n- **Copy methods.**", 1)[0]
    assert "12,000 characters" in discussion
    assert "bounded excerpts" in discussion and "truncation markers" in discussion
    assert "model has **not** received every row" in discussion
    assert "complete verified result, which is what the model reads" not in guide
    assert "model sees the same numbers, units and receipt" not in guide


def test_scientific_workbench_guide_links_audit_and_pack_references():
    guide = GUIDE.read_text(encoding="utf-8")
    assert "(SCIENTIFIC_CORRECTNESS_AUDIT.md)" in guide
    # Only the original 16 packs belong to the fixed inventory table.
    for pack in PINNED_PACKS:
        title = PACKS[pack][0]
        heading = f"### {title} (`{pack}`)"
        assert heading in guide, f"Missing pack section: {pack}"
        section = guide.split(heading, 1)[1].split("\n### ", 1)[0]
        reference = "](biology/biology.md)" if pack == "biology" else "](SCIENTIFIC_"
        assert reference in section, f"Missing pack reference: {pack}"
