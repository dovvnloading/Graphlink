"""Non-timing structural checks for the ADR-019 live-browser harness."""

from __future__ import annotations

import json
import os

import pytest

from backend.chat_library import get_all_chats, load_chat_row
from backend.tests.perf.graph_factory import LARGE, TYPICAL
from backend.tests.perf.live_backend import rss_bytes, seed


@pytest.mark.parametrize("workload", (TYPICAL, LARGE))
def test_saved_live_fixtures_are_factory_graphs_with_a_versioned_rerun_root(
    tmp_path, capsys, workload,
):
    seed(tmp_path, workload.node_count)
    metadata = json.loads(capsys.readouterr().out)
    assert metadata["name"] == "live_" + workload.name
    assert metadata["node_count"] == workload.node_count
    assert len(get_all_chats(tmp_path / "chats.db")) == 2

    normal = load_chat_row(tmp_path / "chats.db", metadata["load_chat_id"])
    rerun = load_chat_row(tmp_path / "chats.db", metadata["rerun_chat_id"])
    assert normal is not None and rerun is not None
    assert len(normal["data"]["nodes"]) == workload.node_count - workload.chart_count
    assert len(normal["data"]["charts"]) == workload.chart_count
    assert len(rerun["data"]["nodes"]) == len(normal["data"]["nodes"])
    edited = [
        node for node in rerun["data"]["nodes"]
        if "ADR019_RERUN_ROOT_" in str(node.get("raw_content", ""))
    ]
    assert len(edited) == 1
    assert len(edited[0]["versions"]) >= 2


def test_live_rss_measurement_reports_actual_process_residence():
    assert rss_bytes(os.getpid()) > 0
