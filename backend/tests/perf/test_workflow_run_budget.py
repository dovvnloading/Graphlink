"""Workflow-specific extension of the ADR-019 watchdog (unchanged ceiling)."""

import asyncio
import json

from backend import chat_library
from backend.events import SessionBus
from backend.tests.perf.graph_factory import build_graph
from backend.tests.perf.test_loop_watchdog import CI_STALL_CEILING_MS, _max_stall_ms


def test_500_node_scene_with_100_step_workflow(tmp_path):
    async def scenario():
        doc = build_graph(node_count=399, content_bytes=1200, chart_count=10,
                          image_count=0, extra_edges=200)
        source = doc.add_note(0, 0)
        doc.set_note_content(source.id, "workflow budget input " * 60)
        targets = []
        for index in range(100):
            target = doc.add_note(300, 200 * index)
            doc.bind_note_text_input(source.id, target.id)
            targets.append(target.id)
        assert len(doc.nodes) == 500

        bus = SessionBus("workflow-budget")
        bus.register_topic("scene", doc.scene_payload)
        chat_library.register_chat_library(bus, tmp_path / "graphs.db", doc,
                                           autosave_interval_seconds=None)

        async def send(intent, *args):
            return await bus.dispatch_intent("scene", intent, list(args))

        preview = await send("previewGraphRun", targets)
        assert len(preview["steps"]) == 100
        result = await send("executeGraphRun", targets, preview["previewToken"], "workflow-budget")
        assert result["status"] == "completed"
        assert len(result["steps"]) == 100
        assert all(step["status"] == "completed" for step in result["steps"])
        assert all(doc.nodes[node_id].content == source.content for node_id in targets)
        assert doc.current_chat_id is not None

        # Use the established two-sample class-catcher, not a new threshold.
        operations = [lambda: json.dumps(doc.scene_payload()),
                      lambda: json.dumps(doc.take_dirty_patch_ops())]
        best = min([await _max_stall_ms(operations) for _ in range(2)])
        assert best <= CI_STALL_CEILING_MS, (
            f"500-node / 100-step workflow publish stalled {best:.1f} ms "
            f"(existing CI ceiling {CI_STALL_CEILING_MS:.0f} ms)"
        )

    asyncio.run(scenario())
