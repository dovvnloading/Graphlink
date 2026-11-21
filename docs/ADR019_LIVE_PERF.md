# ADR-019: live performance measurements (report-only)

This harness fills the four ADR-019 budget rows that the existing
backend-only benchmarks cannot measure: **cold start**, **opening a saved
chat**, **canvas frame time** and **backend/renderer memory**. It uses
the same deterministic 100-node \`TYPICAL\` and 500-node \`LARGE\`
fixtures as \`backend/tests/perf/graph_factory.py\`, rather than a
mock canvas or randomly generated notes.

## Run locally

Use Python 3.12 and Node 24, from the repository root:

\`\`\`sh
python -m pip install -r requirements.txt
npm --prefix web_ui ci
npm --prefix web_ui run build
cd web_ui && npx playwright install chromium && cd ..
node web_ui/scripts/measure-adr019-live.mjs --output perf-results/adr019-live.json
\`\`\`

For a custom Python executable, set \`GRAPHLINK_PERF_PYTHON\` before
running Node. The optional \`--keep-profile\` flag preserves the isolated
temporary directory, including backend logs and SQLite data, for debugging
(a path is printed). Otherwise it is deleted on completion. The runner
requires \`web_ui/dist/app/index.html\` and built assets; it never runs
Vite's development server.

## What runs

For **each** workload, the runner creates a fresh temporary profile and:

1. Constructs the deterministic graph factory's real \`SceneDocument\`.
   It serializes that graph via \`build_chat_data\` and inserts it into
   the isolated \`chats.db\` with \`save_chat_atomically_row\`. It also
   saves a second full-size factory graph with a versioned, edited user
   message as a re-run fixture. Both fixtures contain the normal chart,
   image and cross-link proportions for their size.
2. Starts the **real** \`backend.app.create_app\` under uvicorn on an
   ephemeral loopback port, serving the already-built SPA from
   \`web_ui/dist/app\`. Both settings and databases are explicitly
   rooted in the temporary profile. The local test instance uses the
   backend's existing test-only unauthenticated mode; it is never exposed
   on a non-loopback interface.
3. Launches a fresh headless Playwright Chromium at a 1440×900 viewport
   and waits for the actual React Flow canvas, the SPA's **open**
   WebSocket state and onboarding dismissal. \`cold_start_to_interactive_ms\`
   starts immediately before backend process spawn and ends when that
   canvas is usable. Seeding, dependency installation and SPA compilation
   are **not** in the cold-start window; Chromium launch **is**.
4. Opens the first saved chat through the actual Chat Library UI
   (\`Open chat\`), measuring click-to-visible populated canvas after
   its lazy node views have settled. The overview's 100/500-node count is
   asserted; counting mounted \`.react-flow__node\` elements would be
   wrong because React Flow virtualizes off-screen nodes. It reports
   \`saved_chat_open_ms\`, including the 500-node workload.
5. Samples successive \`requestAnimationFrame\` intervals while
   Playwright performs a real pointer-drag **pan** and clicks Zoom Out
   and Zoom In. After opening the second saved chat, a second normal
   browser WebSocket uses the public \`previewChatRerun\`,
   \`startChatRerun\` and \`getChatRerunStatus\` intents. The real re-run
   orchestration commits node versions and publishes scene changes into
   the visible SPA while another rAF sampler runs. The batch must finish
   successfully with the expected completed reply count.
6. Reads operating-system resident/working-set memory for the backend
   process and the sum of Chromium processes whose CDP type is
   \`renderer\`. It also reports \`JSHeapUsedSize\` via Chromium CDP
   \`Performance.getMetrics\`. Memory is sampled after the first saved
   chat settles and after the re-run finishes.

**Only external model completions are replaced** in the isolated
benchmark backend with 120-ms-spaced, 1200-character synthetic replies.
This makes batch-re-run rendering repeatable and offline, without
configured LLMs, network dependency, token bills or variable model
latency. Production modules and user-visible behavior are unchanged.
This is a **UI/backend re-run cost**, not a model inference benchmark.

## Reading the report

The output is JSON with the same top-level reporting structure as
\`backend/tests/perf/baseline.json\`: \`_comment\`, \`measured_at\`,
\`measured_on\` and \`workloads\`. The new workload names are
\`live_typical\` (100 nodes) and \`live_large\` (500 nodes).
Each includes its actual node and edge count, cold-start and open
durations, frame interval sample counts and p50/p95/p99/max in
milliseconds for **pan/zoom** and **re-run**, frames slower than
33.33 ms, completed re-run reply count and batch duration, plus memory
figures in MiB (\`backend_rss_mib\`, \`renderer_rss_mib\`,
\`renderer_js_heap_mib\`, and post-re-run equivalents).

rAF intervals are a **browser-observed frame-cadence proxy**: they
include scheduler jitter, background work and any missed refreshes.
They are not a GPU render-duration trace or an instrumented React
commit-time measurement. Renderer RSS includes all renderer processes
reported by Chromium for this *fresh, single-browser* run and is not a
whole-browser/GPU-process memory total. JS heap is separate from OS
resident memory; do not add it to RSS. Compare numbers only between
similarly configured machines/runners, Chromium versions and display
settings.

The independent \`.github/workflows/nightly-perf.yml\` live-browser job
uploads \`adr019-live-perf\` as a nightly/manual artifact. The measurement
step is **report-only**, with no regression threshold and no comparison
to \`backend/tests/perf/baseline.json\`. That committed file, its
existing \`check_baseline.py\` gate, and the four original reference
workloads remain untouched. A failed measurement is visible in the
nightly log and report \`error\` field, not silently replaced by zeroes.
