"""Versioned table outputs through the same scene commands the frontend sends."""

import asyncio

import pytest

from backend.events import IntentValidationError
from backend.tests.conftest import client_row
from backend.tests.test_canvas import make_bus


def test_table_output_fan_out_refresh_restore_and_stale_compare():
    async def scenario():
        bus, doc, recorder = make_bus()

        async def send(name, *args):
            return await bus.dispatch_intent("scene", name, list(args))

        def row(node_id):
            nodes = doc.published_scene_payload()["nodes"]
            return client_row(next(n for n in nodes if n["id"] == node_id))

        source = await send("addTable", 10, 20, "Batch,Units\nEast,3\nWest,8\nEast,4")
        chart = await send("addTableOutput", source, "chart", "Batch", "Units")
        report = await send("addTableOutput", source, "artifact", "Batch", "Units")
        assert set(doc.nodes) == {source, chart, report}
        assert row(chart)["chartData"]["labels"] == ["East", "West"]
        assert row(chart)["chartData"]["values"] == [7, 8]
        first_markdown = row(report)["artifactContent"]
        assert "East" in first_markdown and "West" in first_markdown
        original_csv = row(source)["content"]
        await send("setTableCsv", source, original_csv.replace("East,4", "East,10"), original_csv)
        assert row(chart)["tableInputStatus"] == row(report)["tableInputStatus"] == "stale"

        await send("runTableOutput", chart)
        assert row(chart)["tableInputStatus"] == "current"
        assert row(chart)["chartData"]["values"] == [13, 8]
        assert row(report)["artifactContent"] == first_markdown
        assert row(report)["tableInputStatus"] == "stale"
        recorder.messages.clear()
        history_length = len(doc.command_log)
        history = await send("getTableOutputHistory", chart)
        assert history["previous"]["chartData"]["values"] == [7, 8]
        assert history["current"]["chartData"] == row(chart)["chartData"]
        assert recorder.messages == []
        assert len(doc.command_log) == history_length

        await send("restoreTableOutput", chart, history["revisionToken"])
        assert row(chart)["chartData"]["values"] == [7, 8]
        assert row(chart)["tableInputStatus"] == "stale"
        assert row(chart)["tableOutputVersion"] > history["current"]["version"]
        with pytest.raises(IntentValidationError):
            await send("restoreTableOutput", chart, history["revisionToken"])
        assert recorder.topics_seen() == ["scene"]
        assert len(doc.command_log) == history_length + 1
        await send("undo")
        assert row(chart)["chartData"]["values"] == [13, 8]
        await send("redo")
        assert row(chart)["chartData"]["values"] == [7, 8]

        await send("runTableOutput", report)
        report_history = await send("getTableOutputHistory", report)
        assert report_history["previous"]["markdown"] == first_markdown
        refreshed = report_history["current"]["markdown"]
        assert refreshed == row(report)["artifactContent"] and refreshed != first_markdown
        await send("setArtifactContent", report, "My revised interpretation", refreshed)
        assert row(report)["tableOutputEdited"]
        for command, args in [
            ("runTableOutput", [report]),
            ("restoreTableOutput", [report, report_history["revisionToken"]]),
        ]:
            with pytest.raises(IntentValidationError):
                await send(command, *args)
        assert row(report)["artifactContent"] == "My revised interpretation"
        assert await send("getTableOutputHistory", report) == report_history

    asyncio.run(scenario())


def test_bad_table_requests_leave_the_last_published_scene_and_history_intact():
    async def scenario():
        bus, doc, recorder = make_bus()

        async def send(name, *args):
            return await bus.dispatch_intent("scene", name, list(args))

        source = await send("addTable", 0, 0, "Group,Cost\nA,2")
        before = doc.scene_payload()
        history_length = len(doc.command_log)
        recorder.messages.clear()
        for command, args in [
            ("addTable", ["left", 0, "Group,Cost\nB,7"]),
            ("addTable", [float("inf"), 0, "Group,Cost\nB,7"]),
            ("addTable", [1, 0, "A,B\nx,y,z"]),
            ("setTableCsv", [source, "Group,Cost\nA,3", "an older value"]),
            ("addTableOutput", [source, "code", "Group", "Cost"]),
            ("addTableOutput", [source, "chart", "missing column", "Cost"]),
            ("runTableOutput", [source]),
            ("restoreTableOutput", [source]),
            ("getTableOutputHistory", [source]),
        ]:
            with pytest.raises(IntentValidationError) as raised:
                await send(command, *args)
            assert str(raised.value).strip()
        assert doc.scene_payload() == before
        assert len(doc.command_log) == history_length
        assert recorder.messages == []

    asyncio.run(scenario())


def test_restore_rejects_a_comparison_from_an_abandoned_undo_branch():
    async def scenario():
        bus, doc, _ = make_bus()

        async def send(name, *args):
            return await bus.dispatch_intent("scene", name, list(args))

        source = await send("addTable", 0, 0, "Group,Amount\nA,1")
        output = await send("addTableOutput", source, "chart", "Group", "Amount")
        original = doc.nodes[source].content
        await send("setTableCsv", source, "Group,Amount\nA,2", original)
        await send("runTableOutput", output)
        abandoned = await send("getTableOutputHistory", output)

        await send("undo")  # Output returns to its first version.
        await send("undo")  # Source returns to its original CSV.
        await send("setTableCsv", source, "Group,Amount\nA,3", original)
        await send("runTableOutput", output)
        active = await send("getTableOutputHistory", output)
        assert active["current"]["version"] == abandoned["current"]["version"]
        assert active["revisionToken"] != abandoned["revisionToken"]
        with pytest.raises(IntentValidationError):
            await send("restoreTableOutput", output, abandoned["revisionToken"])
        assert await send("getTableOutputHistory", output) == active

    asyncio.run(scenario())


def test_contributors_page_the_original_source_rows_without_editing_or_publishing():
    async def scenario():
        bus, doc, recorder = make_bus()

        async def send(name, *args):
            return await bus.dispatch_intent("scene", name, list(args))

        entries = [f"Rest,{i}" if i % 2 == 0 else f"North  East,0{i}" for i in range(1, 104)]
        source = await send("addTable", 0, 0, "Region,Hours\n" + "\n".join(entries))
        report = await send("addTableOutput", source, "artifact", "Region", "Hours")
        chart = await send("addTableOutput", source, "chart", "Region", "Hours")

        for output in (report, chart):
            recorder.messages.clear()
            history_size = len(doc.command_log)
            version = (await send("getTableOutputHistory", output))["current"]
            first = await send("getTableContributors", output, "North East", 0)
            second = await send("getTableContributors", output, "North East", 50)
            assert (first["totalRows"], first["offset"], second["offset"]) == (52, 0, 50)
            assert first["sourceHash"] == second["sourceHash"] == version["sourceHash"]
            assert (first["group"], first["labelColumn"], first["valueColumn"]) == ("North East", "Region", "Hours")
            assert len(first["rows"]) == 50
            assert first["rows"] + second["rows"] == [
                {"rowNumber": i, "value": f"0{i}"} for i in range(1, 104, 2)
            ]
            for group, offset in [("North East", -1), ("North East", 52), ("North East", False), ("Unknown", 0)]:
                with pytest.raises(IntentValidationError):
                    await send("getTableContributors", output, group, offset)
            assert len(doc.command_log) == history_size
            assert recorder.messages == []

        previous_csv = doc.nodes[source].content
        await send("setTableCsv", source, "Region,Hours\nNorth East,9", previous_csv)
        with pytest.raises(IntentValidationError):
            await send("getTableContributors", report, "North East", 0)
        await send("runTableOutput", report)
        current = await send("getTableContributors", report, "North East", 0)
        assert current["rows"] == [{"rowNumber": 1, "value": "9"}]

        generated = (await send("getTableOutputHistory", report))["current"]["markdown"]
        await send("setArtifactContent", report, "Analyst's revision", generated)
        with pytest.raises(IntentValidationError):
            await send("getTableContributors", report, "North East", 0)
        await send("removeNodes", [source])
        with pytest.raises(IntentValidationError):
            await send("getTableContributors", chart, "North East", 0)

    asyncio.run(scenario())
