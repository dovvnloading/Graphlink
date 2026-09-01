"""Builds the demo graph the README screenshots are taken of.

WHY THIS EXISTS. The screenshots in assets/screenshots/ show a populated
canvas, and every populated canvas needs content - so re-shooting them after
a UI change used to mean having a provider configured, running a real build,
and hoping the output was presentable. That is why they went stale.

The content here is written by hand and the graph is assembled through the
real SceneDocument API - the same add_chat_node/add_code_sandbox_node/
add_plan_node calls the running app makes - then serialized with the real
save path (build_chat_data) into a real chats.db row. The app that opens it
cannot tell it apart from a graph a person built, because structurally it is
one.

THE FIXTURE ITSELF is a production incident investigation: an API's p99
latency regressed 8x after a deploy, and the graph is the debugging session
- competing hypotheses as sibling branches, a thinking node, a SQL run with
its output, cited web research on the index behaviour at fault, two charts
of the incident's real shape, a Builder run that bisected the deploy, and
the decision note. Dense on purpose: the canvas is the product, and a
five-node graph does not demonstrate why a canvas beats a transcript.

USAGE. web_ui/scripts/capture-screenshots.mjs calls this; nothing in
backend/ imports it.

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

QUESTION = (
    "p99 on /v1/events jumped from 180ms to 1.4s after Tuesday's deploy. "
    "APM shows nothing obvious - CPU flat, no error spike. Where do we start?"
)

HYPOTHESIS_DB = (
    "Start at the database, because an 8x p99 with flat CPU is the signature "
    "of queries waiting, not code computing. Pull pg_stat_statements ordered "
    "by mean_exec_time and diff it against last week's snapshot - a regressed "
    "plan will be sitting at the top. Tuesday's deploy touched the events "
    "repository layer, so any query it rewrote is suspect first."
)

HYPOTHESIS_SERIALIZATION = (
    "Check the response path before blaming the database. The deploy bumped "
    "the JSON serializer, and /v1/events returns the largest payloads in the "
    "API. If p50 moved too, serialization is in play; if only p99 moved, it "
    "is almost certainly a query - tail latency is where lock waits and bad "
    "plans live."
)

HYPOTHESIS_ROLLBACK = (
    "Roll back first and diagnose offline. Every hour at 1.4s p99 is costing "
    "us SLO budget, and the deploy is the only variable that changed."
)

THINKING = (
    "p50 held at 140ms while p99 went to 1.4s - that rules out the "
    "serializer, which taxes every request equally. A tail-only regression "
    "with flat CPU means a subset of requests are waiting on something. The "
    "deploy changed the events lookup from an exact match to a prefix "
    "search. A LIKE with a leading parameter cannot use the b-tree index "
    "unless the operator class supports it... check text_pattern_ops before "
    "assuming the planner is broken."
)

FOLLOW_UP = "Pull the top offenders from pg_stat_statements and show me the plans."

SANDBOX_CODE = """import psycopg

SQL = '''
SELECT left(query, 60) AS query,
       calls,
       round(mean_exec_time::numeric, 1) AS mean_ms,
       round(max_exec_time::numeric)     AS max_ms
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 4;
'''

with psycopg.connect("dbname=events_prod") as conn:
    for row in conn.execute(SQL):
        print(row)
"""

SANDBOX_OUTPUT = """('SELECT * FROM events WHERE stream_id LIKE $1 ...', 48211, 1391.7, 4210)
('SELECT * FROM events WHERE org_id = $1 AND crea', 91442, 12.4, 88)
('INSERT INTO events (id, stream_id, org_id, kind', 220380, 3.1, 41)
('SELECT count(*) FROM events WHERE org_id = $1', 8091, 2.8, 19)

EXPLAIN ANALYZE, offender #1:
  Seq Scan on events  (cost=0.00..412048.90 rows=1210 width=488)
    Filter: (stream_id ~~ ($1 || '%'))
    Rows Removed by Filter: 8,214,332
  Execution Time: 1389.221 ms

-> the prefix LIKE is seq-scanning 8.2M rows. The b-tree on
   stream_id is unused: default opclass, non-C collation.
"""

RESEARCH_QUERY = "postgres LIKE prefix query not using btree index text_pattern_ops"

RESEARCH_ANSWER = (
    "A b-tree index only serves `LIKE 'abc%'` when the column's operator "
    "class sorts by byte order. Under a non-C collation (this database is "
    "`en_US.UTF-8`), the default class cannot guarantee prefix ranges are "
    "contiguous, so the planner falls back to a sequential scan.\n\n"
    "Two standard fixes:\n\n"
    "- **`text_pattern_ops`** - a second index with the pattern operator "
    "class: `CREATE INDEX CONCURRENTLY events_stream_prefix_idx ON events "
    "(stream_id text_pattern_ops);` Anchored-prefix queries use it; ordinary "
    "equality keeps the existing index.\n"
    "- **C collation on the column** - one index serves both, but changing "
    "collation rewrites the table and changes sort order for user-visible "
    "listings.\n\n"
    "For an 8M-row hot table, the extra index is the low-risk path: built "
    "CONCURRENTLY it takes no exclusive lock."
)

RESEARCH_SOURCES = [
    {
        "title": "PostgreSQL: Operator Classes and Operator Families",
        "url": "https://www.postgresql.org/docs/current/indexes-opclass.html",
        "snippet": "The operator class text_pattern_ops supports b-tree indexes on LIKE queries when the database does not use the C locale...",
    },
    {
        "title": "Use The Index, Luke - LIKE and prefix search",
        "url": "https://use-the-index-luke.com/sql/where-clause/searching-for-ranges/like-performance-tuning",
        "snippet": "A LIKE expression can use an index only if the search term has a fixed prefix - and only under an index whose sort order matches byte comparison...",
    },
    {
        "title": "pganalyze - Indexing LIKE queries in Postgres",
        "url": "https://pganalyze.com/blog/postgres-like-queries-indexing",
        "snippet": "On non-C collations, add a text_pattern_ops index for anchored patterns; CREATE INDEX CONCURRENTLY avoids blocking writes on large tables...",
    },
]

FINDINGS = (
    "Confirmed: Tuesday's deploy changed the stream lookup from `stream_id "
    "= $1` to `stream_id LIKE $1 || '%'` for the new wildcard subscriptions "
    "feature. Under en_US.UTF-8 the b-tree can't serve the prefix, so every "
    "wildcard subscriber seq-scans 8.2M rows - which is exactly the p99 "
    "population. The `text_pattern_ops` index went out at 14:10; p99 fell "
    "from 1.4s to 210ms within the hour. Keeping the feature, keeping the "
    "index."
)

NOTE_TEXT = (
    "Post-incident actions: (1) text_pattern_ops index is permanent - added "
    "to the schema migration, (2) EXPLAIN-diff gate in CI for queries the "
    "repository layer rewrites, (3) alert on seq-scan rate for tables over "
    "1M rows."
)

PLAN_GOAL = "Bisect Tuesday's deploy and verify the index fix under production load"

PLAN_STEPS = [
    {"id": "s1", "title": "Snapshot pg_stat_statements before and after rollback", "status": "done", "detail": "regression isolated to one query"},
    {"id": "s2", "title": "Bisect the deploy's 14 commits against a staging replay", "status": "done", "detail": "9f2c41e: repository layer LIKE rewrite"},
    {"id": "s3", "title": "Build events_stream_prefix_idx CONCURRENTLY on staging", "status": "done", "detail": "4m 12s, no lock waits"},
    {"id": "s4", "title": "Replay Tuesday's traffic against the indexed table", "status": "done", "detail": "p99 208ms, plan uses index"},
    {"id": "s5", "title": "Chart before/after and write the incident note", "status": "done", "detail": "2 charts, note added"},
]

ACTIVITY = [
    {"tool": "code.run_python", "summary": "pg_stat_statements diff -> 1 regressed query (1391ms mean)", "outcome": "ok", "stepId": "s1", "elapsedMs": 2140},
    {"tool": "code.run_python", "summary": "bisect 14 commits -> 9f2c41e (LIKE rewrite)", "outcome": "ok", "stepId": "s2", "elapsedMs": 312840},
    {"tool": "code.run_python", "summary": "CREATE INDEX CONCURRENTLY -> 252s, valid", "outcome": "ok", "stepId": "s3", "elapsedMs": 252190},
    {"tool": "code.run_python", "summary": "traffic replay -> p99 208ms (was 1418ms)", "outcome": "ok", "stepId": "s4", "elapsedMs": 184220},
    {"tool": "chart.generate", "summary": 'type="line", 14 days of p99', "outcome": "ok", "stepId": "s5", "elapsedMs": 1480},
    {"tool": "graph.create_node", "summary": 'kind="note", title="Post-incident actions"', "outcome": "ok", "stepId": "s5", "elapsedMs": 96},
]

# Fourteen days of p99, and the story is in the shape: stable baseline, the
# Tuesday deploy, six days of regression, the index landing, recovery.
CHART_P99 = {
    "version": 1,
    "type": "line",
    "title": "/v1/events p99 latency, 14 days",
    "labels": ["May 6", "7", "8", "9", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19"],
    "values": [176, 181, 178, 184, 179, 1442, 1466, 1390, 1418, 1445, 1451, 212, 196, 188],
    "xAxis": "Day",
    "yAxis": "p99 (ms)",
}

CHART_ENDPOINTS = {
    "version": 1,
    "type": "bar",
    "title": "p99 by endpoint during the incident",
    "labels": ["/v1/events", "/v1/search", "/v1/webhooks", "/v1/orgs", "/v1/users"],
    "values": [1418, 611, 243, 218, 187],
    "xAxis": "Endpoint",
    "yAxis": "p99 (ms)",
}


def build_document() -> SceneDocument:
    """The graph. Positions are hand-placed so the screenshot framing is
    stable - an auto-layout pass would reflow the moment a node's measured
    height changed. Column pitch ~520 against a 422px node column and 480px
    charts; nothing is taller than ~450px under the current size caps."""
    doc = SceneDocument()

    # -- Triage: the question and three competing hypotheses, framed. ------
    question = doc.add_chat_node(-220, -560, QUESTION, True)
    hyp_db = doc.add_chat_node(-740, -330, HYPOTHESIS_DB, False, question.id)
    hyp_ser = doc.add_chat_node(-220, -330, HYPOTHESIS_SERIALIZATION, False, question.id)
    hyp_rb = doc.add_chat_node(300, -330, HYPOTHESIS_ROLLBACK, False, question.id)
    hyp_ser.state.branch_status = "rejected"
    hyp_rb.state.branch_status = "superseded"

    # Placed for the picture beside the branch it narrates; nothing else
    # references it.
    doc.add_thinking_node(900, -330, THINKING, hyp_db.id)

    frame = doc.create_frame([question.id, hyp_db.id, hyp_ser.id, hyp_rb.id])
    frame.title = "Triage: 8x p99 regression"

    # -- The database branch does the real work. ---------------------------
    follow_up = doc.add_chat_node(-740, -60, FOLLOW_UP, True, hyp_db.id)
    sandbox = doc.add_code_sandbox_node(-1290, -520, follow_up.id)
    doc.set_code_sandbox_requirements(sandbox.id, "psycopg[binary]==3.2.1")
    sandbox.state.code_sandbox_prompt = "Top offenders from pg_stat_statements, with plans"
    sandbox.state.code_sandbox_code = SANDBOX_CODE
    sandbox.state.code_sandbox_output = SANDBOX_OUTPUT

    research = doc.add_web_research_node(-1290, 190, follow_up.id)
    doc.start_web_research_run(research.id, RESEARCH_QUERY)
    doc.complete_web_research_run(research.id, {
        "requestId": "req-demo-1",
        "originalQuery": RESEARCH_QUERY,
        "effectiveQuery": RESEARCH_QUERY,
        "answerMarkdown": RESEARCH_ANSWER,
        "sources": [
            {
                "sourceId": f"s{i+1}", "title": src["title"], "url": src["url"],
                "canonicalUrl": src["url"], "snippet": src["snippet"], "rank": i + 1,
                "provider": "searx", "finalUrl": src["url"], "status": "fetched",
                "errorCode": "", "errorMessage": "", "truncated": False,
                "contentHash": f"demo{i+1:03d}", "citationCount": 1,
            }
            for i, src in enumerate(RESEARCH_SOURCES)
        ],
        "citations": [],
        "warnings": [],
        "providerSnapshot": {},
    })

    findings = doc.add_chat_node(-740, 220, FINDINGS, False, follow_up.id)

    # -- Evidence: the incident's shape, charted. --------------------------
    doc.add_chart_node(-220, 120, findings.id, "line", CHART_P99)
    doc.add_chart_node(320, 120, findings.id, "bar", CHART_ENDPOINTS)

    # -- The Builder run that did the bisect, and what came out of it. -----
    plan = doc.add_plan_node(1400, -520, PLAN_GOAL, mode="copilot",
                             max_steps=8, max_tokens=80_000, max_wall_seconds=1_800)
    doc.set_plan_steps(plan.id, PLAN_STEPS)
    plan.state.builder_status = "done"
    plan.state.builder_run_id = "run-demo-1"
    plan.state.builder_spent_steps = 5
    plan.state.builder_spent_tokens = 42_710
    plan.state.builder_spent_wall_seconds = 763
    plan.state.builder_activity = ACTIVITY

    note = doc.add_note(1400, -30)
    doc.set_note_content(note.id, NOTE_TEXT)

    return doc


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python tools/seed_demo_graph.py <chats.db path>")
    db_path = Path(sys.argv[1])
    db_path.parent.mkdir(parents=True, exist_ok=True)

    document = build_document()
    chat_data = build_chat_data(document)
    notes_data = chat_data.pop("notes_data", [])
    pins_data = chat_data.pop("pins_data", [])

    chat_id, _updated = save_chat_atomically_row(
        db_path, None, "Events API p99 regression", chat_data, notes_data, pins_data,
    )
    print(f"seeded chat {chat_id} with {len(document.nodes)} nodes into {db_path}")


if __name__ == "__main__":
    main()
