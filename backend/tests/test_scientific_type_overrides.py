"""CSV type overrides resolve to exactly one column (#1096)."""

from __future__ import annotations

import pytest

from graphlink_plugins.scientific.data_table import parse_table, use_table_store

SAME_BASE = b"Temperature,Temperature (degC)\n12,22\n13,23\n"


def _types(source, hints, **kwargs):
    table = parse_table(source, hints=hints, preview_only=True, **kwargs)
    return [(column["label"], column["type"], column["unit"]) for column in table["columns"]]


def test_exact_label_hint_leaves_the_unit_qualified_sibling_inferred():
    assert _types(SAME_BASE, {"Temperature": "text"}) == [
        ("Temperature", "text", ""), ("Temperature (degC)", "numeric", "degC")]


def test_hint_on_the_qualified_label_affects_only_that_column():
    assert _types(SAME_BASE, {"Temperature (degC)": "text"}) == [
        ("Temperature", "numeric", ""), ("Temperature (degC)", "text", "degC")]


def test_numeric_hint_on_one_heading_does_not_break_a_text_sibling():
    source = b"Site,Site (code)\n1,north\n2,south\n"
    assert _types(source, {"Site": "numeric"}) == [("Site", "numeric", ""), ("Site (code)", "text", "code")]


def test_ambiguous_base_name_alias_is_rejected():
    source = b"Temperature (degC),Temperature (K)\n1,274\n2,275\n"
    with pytest.raises(ValueError, match="does not identify one column"):
        _types(source, {"Temperature": "text"})


def test_unique_base_name_alias_still_works():
    assert _types(b"Pressure (kPa),Site\n101,a\n", {"Pressure": "text"})[0] == ("Pressure (kPa)", "text", "kPa")


def test_two_hints_for_one_column_are_rejected():
    with pytest.raises(ValueError, match="Two type overrides"):
        _types(b"Pressure (kPa)\n101\n", {"Pressure": "text", "Pressure (kPa)": "numeric"})


def test_committed_import_matches_the_preview(tmp_path):
    with use_table_store(str(tmp_path)):
        committed = parse_table(SAME_BASE, hints={"Temperature": "text"})
    assert [column["type"] for column in committed["columns"]] == ["text", "numeric"]
    assert committed["columns"][1]["unit"] == "degC"
