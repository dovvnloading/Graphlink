"""Asset-backed scientific result tables: worker, graph, persistence and CSV regressions."""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import json

import pytest

from backend.asset_store import AssetStore
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data
from backend.tests.test_scientific_workbench import (
    dispatch, envelope, grant, run, session as _shared_session,
)
# Re-export the imported fixture for pytest without an unused import.
session = _shared_session
from graphlink_plugins.scientific import workbench
from graphlink_plugins.scientific.core import (
    Operation, calculate_result, readable_result, run_operation,
)
from graphlink_plugins.scientific.data_table import (
    CSV_CHUNK_BYTES, numeric_column, read_table, table_csv_chunk, table_metadata_valid,
)
from graphlink_plugins.scientific.execution import execute_in_process
from graphlink_plugins.scientific.graph_run_adapter import prepare


ROW_COUNT = 12_000


def large_table_result(_inputs: dict) -> dict:
    """A deterministic, picklable calculator, including all five column kinds."""
    rows = []
    for i in range(ROW_COUNT):
        comment = "=SUM(1,2)" if i == 0 else "two, lines\nβ" if i == 1 else f"sample-{i}-β"
        rows.append([i, "A" if i % 2 == 0 else "B", i / 2, i % 2 == 0,
                     "2026-11-19" if i % 2 == 0 else "2026-11-20", comment])
    return {
        "summary": "Generated complete observations.",
        "values": {"row_count": ROW_COUNT},
        "table": {
            "columns": ["Index", "Group", "Distance (m)", "Flag", "Observed", "Comment"],
            "rows": rows,
        },
    }


def _large_operation() -> Operation:
    return Operation("generated_large_table", "Generated large table", "",
                     (), {}, large_table_result)


def test_large_operation_table_is_typed_and_preserves_exact_rows():
    result = run_operation("statistics", _large_operation(), {})
    meta = result["tableAsset"]
    assert "table" not in result
    assert readable_result({"version": 1, "latest": result}, "statistics")
    assert table_metadata_valid(meta) and meta["rowCount"] == ROW_COUNT
    assert [column["type"] for column in meta["columns"]] == [
        "numeric", "categorical", "numeric", "boolean", "datetime", "text",
    ]
    assert len(result["tableAsset"]["preview"]) == 8
    full = read_table(meta)
    assert full["rows"][0] == [0, "A", 0.0, True, "2026-11-19", "=SUM(1,2)"]
    assert full["rows"][-1][2] == (ROW_COUNT - 1) / 2
    assert numeric_column(meta, 2, start=1024, end=1124) == [
        i / 2 for i in range(1024, 1124)
    ]
    with pytest.raises(ValueError, match="1024"):
        numeric_column(meta, 2, start=0, end=1025)
    with pytest.raises(ValueError, match="100,000 rows"):
        oversized = Operation(
            "too_large_result", "Too large", "", (), {},
            lambda _: {"summary": "", "values": {}, "table": {
                "columns": ["A"], "rows": [[1]] * 100_001,
            }},
        )
        run_operation("statistics", oversized, {})


def test_small_legacy_inline_result_is_not_migrated():
    operation = Operation("small_table_result", "Small", "", (), {}, lambda _: {
        "summary": "small", "values": {"count": 2},
        "table": {"columns": ["x"], "rows": [[1], [2]]},
    })
    result = run_operation("core", operation, {})
    assert "table" in result and "tableAsset" not in result
    assert readable_result({"version": 1, "latest": result}, "core")


def test_generated_table_csv_pages_cover_all_rows_and_escape_spreadsheets():
    meta = run_operation("statistics", _large_operation(), {})["tableAsset"]
    offset = 0
    pages = []
    while True:
        page = table_csv_chunk(meta, offset)
        assert page["nextOffset"] > offset
        pages.append(base64.b64decode(page["chunk"]))
        offset = page["nextOffset"]
        if page["done"]:
            assert page["totalBytes"] == offset
            break
    assert len(pages) >= 2 and len(pages[0]) == CSV_CHUNK_BYTES
    decoded = b"".join(pages).decode("utf-8")
    rows = list(csv.reader(io.StringIO(decoded, newline="")))
    assert len(rows) == ROW_COUNT + 1
    assert rows[0] == [c["label"] for c in meta["columns"]]
    assert rows[1] == ["0", "A", "0.0", "true", "2026-11-19", "'=SUM(1,2)"]
    assert rows[2][-1] == "two, lines\nβ"
    assert rows[-1][0] == str(ROW_COUNT - 1)
    with pytest.raises(ValueError, match="offset"):
        table_csv_chunk(meta, -1)
    with pytest.raises(ValueError, match="offset"):
        table_csv_chunk(meta, offset + 1)


def test_worker_round_trips_result_asset_without_inline_table():
    async def execute():
        return await execute_in_process(
            "statistics", _large_operation(), {}, {},
            timeout_s=60, cancel_event=asyncio.Event(),
        )

    result = asyncio.run(execute())
    assert "tableAsset" in result and "table" not in result
    assert read_table(result["tableAsset"])["rows"][-1][0] == ROW_COUNT - 1


def test_generated_table_links_exports_and_survives_workspace_restore(session, monkeypatch, tmp_path):
    document, bus, registry, settings = session
    original_operations = workbench.operations
    monkeypatch.setattr(workbench, "operations", lambda pack: (
        (*original_operations(pack), _large_operation())
        if pack == "statistics" else original_operations(pack)
    ))

    # Exercise the intent's grant, recheck and undoable storage while avoiding
    # a second expensive spawn for an already-tested output calculator.
    async def local_worker(pack_id, operation, inputs, sources, **kwargs):
        return calculate_result(
            pack_id, operation, inputs, sources,
            input_units=kwargs["input_units"],
            uncertainty_inputs=kwargs["uncertainty_inputs"],
            uncertainty_method=kwargs["uncertainty_method"],
        )

    monkeypatch.setattr(workbench, "execute_in_process", local_worker)
    grant(bus, "statistics")
    source = dispatch(bus, "executePlugin", "Statistics")
    assert run(bus, "statistics", source, "generated_large_table", {}) == {"ok": True}
    saved_state = envelope(document, source)
    result = saved_state["latest"]
    meta = result["tableAsset"]
    assert saved_state["table"] is None
    assert not result.get("table") and meta["rowCount"] == ROW_COUNT
    assert len(json.dumps(result)) < 512 * 1024

    page = dispatch(bus, "invokePluginIntent", "science_statistics", "export_table_csv", {
        "node_id": source, "expected_run": saved_state["runToken"], "offset": 0,
    })
    assert page["ok"] and page["nextOffset"] == CSV_CHUNK_BYTES
    assert not page["done"] and page["totalBytes"] > CSV_CHUNK_BYTES
    assert dispatch(bus, "invokePluginIntent", "science_statistics", "export_table_csv", {
        "node_id": source, "table_source": "bad",
    })["ok"] is False

    target = dispatch(bus, "executePlugin", "Statistics", source)
    citation = {
        "nodeId": source, "sourceKey": saved_state["nodeKey"],
        "selector": ["result_table"],
        "resultHash": result["provenance"]["resultHash"],
        "receiptHash": result["provenance"]["receiptHash"],
    }
    assert run(bus, "statistics", target, "grouped_table_summary", {
        "table": meta, "value_column": 2, "group_column": 1,
    }, sources={"table": citation}) == {"ok": True}
    grouped = envelope(document, target)["latest"]
    assert grouped["values"]["row_count"] == ROW_COUNT
    assert grouped["values"]["group_count"] == 2
    assert grouped["provenance"]["sources"]["table"]["selector"] == ["result_table"]
    assert dispatch(bus, "invokePluginIntent", "science_statistics", "configure_runner", {
        "node_id": target, "operation_id": "grouped_table_summary",
        "inputs": {"table": meta, "value_column": 2, "group_column": 1},
        "sources": {"table": citation},
    })["ok"]
    op, resolved_inputs, resolved_sources, digest, same = prepare(
        document, document.nodes[target], lambda _: True,
    )
    assert op.id == "grouped_table_summary" and resolved_inputs["table"] == meta
    assert resolved_sources["table"]["selector"] == ["result_table"]
    assert digest and same

    third = dispatch(bus, "executePlugin", "Statistics", source)
    chosen = dispatch(bus, "invokePluginIntent", "science_statistics", "table_column", {
        "node_id": third, "source_node_id": source, "table_source": "result",
        "column": 2, "start": 500, "end": 600,
        "expected_hash": citation["resultHash"], "expected_receipt": citation["receiptHash"],
    })
    assert chosen["ok"] and chosen["values"] == [i / 2 for i in range(500, 600)]
    assert chosen["reference"]["selector"] == ["result_table_column", 2, "reject", 500, 600]
    assert run(bus, "statistics", third, "describe", {"observations": chosen["values"]},
               sources={"observations": chosen["reference"]}) == {"ok": True}

    store = AssetStore(tmp_path / "assets")
    saved = build_chat_data(
        document, asset_store=store, plugin_registry=registry, settings_manager=settings,
    )
    assert store.verify(meta["asset_ref"])
    assert "=SUM(1,2)" not in json.dumps(saved)
    restore_chat_into_document(
        document, {"data": saved}, [], [], asset_store=store,
        plugin_registry=registry, settings_manager=settings,
    )
    restored_source, restored_target, restored_third = list(document.nodes)
    assert envelope(document, restored_source)["latest"]["tableAsset"] == meta
    assert envelope(document, restored_target)["latest"] == grouped
    assert envelope(document, restored_third)["latest"]["values"]["mean"] == pytest.approx(
        sum(chosen["values"]) / len(chosen["values"])
    )
    assert run(bus, "statistics", restored_target, "grouped_table_summary", {
        "table": meta, "value_column": 2, "group_column": 1,
    }, sources={"table": citation}) == {"ok": True}
    op, restored_inputs, restored_sources, digest, _ = prepare(
        document, document.nodes[restored_target], lambda _: True,
    )
    assert op.id == "grouped_table_summary" and restored_inputs["table"] == meta
    assert restored_sources["table"]["selector"] == ["result_table"] and digest
    # A new source run invalidates the pinned generated-table receipt.
    assert run(bus, "statistics", restored_source, "describe", {
        "observations": [1.0, 2.0, 3.0],
    }) == {"ok": True}
    stale = run(bus, "statistics", restored_target, "grouped_table_summary", {
        "table": meta, "value_column": 2, "group_column": 1,
    }, sources={"table": citation})
    assert stale["ok"] is False and "result table" in stale["error"]
