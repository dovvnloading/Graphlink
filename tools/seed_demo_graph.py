"""Builds the demo graph the README screenshots are taken of.

WHY THIS EXISTS. The screenshots in assets/screenshots/ show a populated
canvas - branched conversations, a finished build, code and a chart. Every
one of those needs model output, so re-shooting them after a UI change used
to mean having a provider configured, running a real build, and hoping it
produced something presentable. That is why they went stale: the cost of
re-taking them was an afternoon and a working model, so nobody did, and the
README kept showing a toolbar that no longer exists.

So the content is written by hand here and the graph is assembled through
the real SceneDocument API - the same add_chat_node/add_code_node/
add_plan_node calls the running app makes - then serialized with the real
save path (build_chat_data) into a real chats.db row. Nothing is mocked:
the app that opens this file cannot tell it apart from a graph a person
built, because structurally it is one. The prose in it is written, not
generated, which is the only honest way to put words in a screenshot
without a model.

USAGE. tools/capture_screenshots.mjs calls this; it is not part of the
shipped app and nothing in backend/ imports it.

    python tools/seed_demo_graph.py <chats.db path>
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.chat_library import save_chat_atomically_row  # noqa: E402
from backend.domain.graph import SceneDocument  # noqa: E402
from backend.session_save import build_chat_data  # noqa: E402

# Hand-written, deliberately plausible rather than impressive: a person
# working out whether a library is worth adopting. Short enough to render
# legibly at screenshot scale, long enough to look like real work.
QUESTION = "Is Polars worth switching to from pandas for a 2GB CSV pipeline?"

ANSWER_A = (
    "For a 2GB CSV the answer is usually yes, and the reason is memory rather "
    "than raw speed. Polars streams from disk and holds a columnar Arrow "
    "buffer, so peak RSS tends to land near the working set instead of two "
    "to three times the file size. The migration cost is real though: the "
    "expression API is not a drop-in for chained pandas operations, and "
    "anything reaching into .apply() has to be rewritten."
)

ANSWER_B = (
    "Worth checking the other direction first. If the pipeline is I/O bound "
    "on a single pass, pandas with pyarrow-backed dtypes and chunked reads "
    "closes most of the gap without a rewrite. The case for switching gets "
    "much stronger once you need joins or group-bys over the whole file, "
    "where the query optimiser actually has something to plan."
)

FOLLOW_UP = "Show me the memory difference on a group-by."

SANDBOX_CODE = '''import polars as pl
import pandas as pd

# 2GB synthetic orders file, grouped by region.
lazy = pl.scan_csv("orders.csv").group_by("region").agg(
    pl.col("amount").sum().alias("total"),
    pl.col("order_id").count().alias("orders"),
)
print(lazy.collect().head())
'''

SANDBOX_OUTPUT = """shape: (5, 3)
+-----------+---------------+--------+
| region    | total         | orders |
+-----------+---------------+--------+
| EMEA      | 48291043.22   | 412083 |
| NA        | 61550927.10   | 528114 |
| APAC      | 33902118.87   | 291447 |
| LATAM     | 12447301.65   | 108920 |
| ANZ       |  8110552.40   |  71338 |
+-----------+---------------+--------+

peak memory: polars 2.41 GB / pandas 6.08 GB
elapsed:     polars 11.4 s  / pandas 47.9 s
"""

NOTE_TEXT = (
    "Decision: move the nightly aggregation to Polars, leave the reporting "
    "notebooks on pandas. Revisit if the join step lands in the same job."
)

PLAN_GOAL = "Benchmark Polars against pandas on the orders pipeline and chart the result"

PLAN_STEPS = [
    {"id": "s1", "title": "Generate a representative 2GB orders CSV", "status": "done", "detail": "wrote orders.csv"},
    {"id": "s2", "title": "Run the group-by under both libraries", "status": "done", "detail": "5 regions, 1.4M rows"},
    {"id": "s3", "title": "Record peak memory and wall time", "status": "done", "detail": ""},
    {"id": "s4", "title": "Chart the comparison", "status": "done", "detail": "bar, 2 series"},
    {"id": "s5", "title": "Write up the recommendation", "status": "done", "detail": "note added"},
]

ACTIVITY = [
    {"tool": "code.run_python", "summary": "generate_orders.py -> orders.csv (2.03 GB)", "outcome": "ok", "stepId": "s1", "elapsedMs": 18422},
    {"tool": "code.run_python", "summary": "bench_polars.py -> 11.4 s, peak 2.41 GB", "outcome": "ok", "stepId": "s2", "elapsedMs": 12180},
    {"tool": "code.run_python", "summary": "bench_pandas.py -> 47.9 s, peak 6.08 GB", "outcome": "ok", "stepId": "s2", "elapsedMs": 49310},
    {"tool": "graph.create_node", "summary": 'kind="note", title="Benchmark results"', "outcome": "ok", "stepId": "s3", "elapsedMs": 84},
    {"tool": "chart.generate", "summary": 'type="bar", series=["peak memory (GB)"]', "outcome": "ok", "stepId": "s4", "elapsedMs": 1960},
]

# The shape the wire type declares (ChartDataRow) and canonicalize_chart_data
# produces - labels/values, not a series list. A shape the canonicalizer does
# not recognise round-trips to an empty chart, which is how the first draft of
# this file produced a node that vanished on load.
CHART_DATA = {
    "version": 1,
    "type": "bar",
    "title": "Peak memory, group-by over 2 GB",
    "labels": ["Polars", "pandas"],
    "values": [2.41, 6.08],
    "xAxis": "",
    "yAxis": "GB",
}


def build_document() -> SceneDocument:
    """The graph itself. Positions are hand-placed so the screenshot framing
    is stable - an auto-layout pass would reflow the moment a node's
    measured height changed, and every capture would need re-framing.

    Spacing assumes the real footprints: a 422px node column, a 480px chart,
    and nothing taller than ~450px. Laid out on a ~470px column pitch, the
    whole graph fits one screen without the fit-zoom dropping far enough to
    make the chat text unreadable."""
    doc = SceneDocument()

    # The branch point, and two answers exploring it in different directions.
    question = doc.add_chat_node(0, -300, QUESTION, True)
    answer_a = doc.add_chat_node(-250, -120, ANSWER_A, False, question.id)
    answer_b = doc.add_chat_node(250, -120, ANSWER_B, False, question.id)

    # One branch continues into a real measurement.
    follow_up = doc.add_chat_node(-250, 130, FOLLOW_UP, True, answer_a.id)
    sandbox = doc.add_code_sandbox_node(-260, 310, follow_up.id)
    doc.set_code_sandbox_requirements(sandbox.id, "polars==1.12.0\npandas==2.2.3\npyarrow==17.0.0")
    sandbox.state.code_sandbox_prompt = "Compare peak memory for a group-by over the orders file"
    sandbox.state.code_sandbox_code = SANDBOX_CODE
    sandbox.state.code_sandbox_output = SANDBOX_OUTPUT

    # The other branch is the one that got parked, which is what branch
    # status is for - it stays on the canvas rather than being deleted.
    answer_b.state.branch_status = "superseded"

    # A build, mid-run, next to the work it produced.
    plan = doc.add_plan_node(250, 310, PLAN_GOAL, mode="copilot", max_steps=6, max_tokens=50_000, max_wall_seconds=300)
    doc.set_plan_steps(plan.id, PLAN_STEPS)
    # Landed, not running: a build reloaded from disk while "running" is
    # correctly reported as interrupted by the app, which is honest behaviour
    # and the wrong thing to put in a screenshot captioned "a finished run".
    plan.state.builder_status = "done"
    plan.state.builder_run_id = "run-demo-1"
    plan.state.builder_spent_steps = 5
    plan.state.builder_spent_tokens = 21_884
    plan.state.builder_spent_wall_seconds = 118
    plan.state.builder_activity = ACTIVITY

    chart = doc.add_chart_node(740, 310, plan.id, "bar", CHART_DATA)
    chart.title = "Polars vs pandas"

    note = doc.add_note(740, -120)
    doc.set_note_content(note.id, NOTE_TEXT)

    frame = doc.create_frame([question.id, answer_a.id, answer_b.id])
    frame.title = "Should we switch?"
    return doc


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python tools/seed_demo_graph.py <chats.db path>")
    db_path = Path(sys.argv[1])
    db_path.parent.mkdir(parents=True, exist_ok=True)

    document = build_document()
    chat_data = build_chat_data(document)
    # The payload's own key names - "notes"/"pins" pop nothing and silently
    # save an empty list while the real data rides along inside chat_data,
    # which is how the first run produced a blank note on the canvas.
    notes_data = chat_data.pop("notes_data", [])
    pins_data = chat_data.pop("pins_data", [])

    chat_id, _updated = save_chat_atomically_row(
        db_path, None, "Polars vs pandas", chat_data, notes_data, pins_data,
    )
    print(f"seeded chat {chat_id} with {len(document.nodes)} nodes into {db_path}")


if __name__ == "__main__":
    main()
