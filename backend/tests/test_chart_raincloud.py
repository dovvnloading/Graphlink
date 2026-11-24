"""Raw-observation and normalized KDE fidelity across chart formats."""
from copy import deepcopy
from xml.etree import ElementTree as ET

import pytest

from graphlink_chart_data import (ChartDataError, canonicalize_chart_data,
                                  raincloud_density, raincloud_jitter)
from graphlink_chart_rendering import render_chart_publication

SOURCE = {"type": "boxplot", "groups": [
    {"label": "Control", "values": [1, 2, 3, 4, 5]},
    {"label": "Constant", "values": [7, 7, 7]},
], "analysis": {"distribution": "raincloud"}}


def test_normalized_density_and_stable_observation_offsets():
    values = SOURCE["groups"][0]["values"]
    density = raincloud_density(values)
    assert len(density) == 65
    assert density[0][0] == 1 and density[-1][0] == 5
    assert density[0][1] == pytest.approx(density[-1][1])
    assert max(d for _, d in density) == 1
    assert raincloud_density([7, 7]) == []
    assert values == [1, 2, 3, 4, 5]
    offsets = [raincloud_jitter(i) for i in range(300)]
    assert len(set(offsets)) == 300
    assert 0.15 <= min(offsets) <= max(offsets) <= 0.43


def test_raincloud_contract_preserves_source_and_rejects_other_chart_types():
    original = deepcopy(SOURCE)
    canonical = canonicalize_chart_data(SOURCE)
    assert canonical["groups"] == SOURCE["groups"]
    assert canonicalize_chart_data(canonical) == canonical
    assert SOURCE == original
    with pytest.raises(ChartDataError, match="Raincloud"):
        canonicalize_chart_data({"type": "histogram", "values": [1, 2, 3],
                                 "analysis": {"distribution": "raincloud"}})


@pytest.mark.parametrize("fmt,signature", [("svg", b"<svg"), ("pdf", b"%PDF"), ("png", b"\x89PNG")])
def test_raincloud_publication_formats(fmt, signature):
    spec = canonicalize_chart_data(SOURCE)
    before = deepcopy(spec)
    exported = render_chart_publication("boxplot", spec, width_mm=180, height_mm=100, fmt=fmt)
    assert signature in exported[:300]
    assert b"Chart rendering issue" not in exported
    assert spec == before
    if fmt == "svg":
        root = ET.fromstring(exported)
        labels = " ".join("".join(e.itertext()) for e in root.iter() if e.tag.endswith("}text"))
        assert "Raincloud" in labels
        assert "raw observations" in labels
