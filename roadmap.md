# Graphlink — Advanced Capability Roadmap

> **Status:** Proposed engineering roadmap, not a release plan  
> **Baseline:** [`main` at `bf0fa154`](https://github.com/dovvnloading/Graphlink/commit/bf0fa15414190912b62a8efe909df08613c4d96d), reviewed 2026-11-14  
> **Goal:** Build an exceptionally capable, graph-native, local-first AI workspace. Prioritize new abilities, intelligence, autonomy, control, and extensibility **while the application is in development**. Installer work, launch readiness, marketing, adoption experiments, and cosmetic polish are **not** milestones here.

## Implementation progress

**[Executable text connections · PR #575](https://github.com/dovvnloading/Graphlink/pull/575)** implements the first part of C1. A chat message, plain note, or accepted Drafter document can supply text to another note. The destination runs an exact copy or a word count on demand. It retains its result when the source changes and shows that its input is stale. The connection and its freshness survive a workspace round trip. The PR includes a [working canvas example](https://github.com/dovvnloading/Graphlink/blob/dc40751af806c8fcb9856a57b4f11e1900b22e57/assets/screenshots/note-text-input.png).

**Versioned tables, charts and reports.** The second C1 implementation accepts a local CSV or an edited grid as input. Each chart or Drafter report groups and sums its chosen columns. A change to Hours makes the Hours chart stale while a Cost report stays current. A current group total opens to its source row numbers and original values. Rerun, compare, and restore operate independently per output. SQLite saves and workspace archives keep the relationships and the two most recent generated versions. [Implementation PR #576](https://github.com/dovvnloading/Graphlink/pull/576) · [Canvas example](https://github.com/dovvnloading/Graphlink/blob/77a3f7c5cd88a805ffffd08f373e3562fd8a6e2d/assets/screenshots/table-workflow.png)

**Next development step — run several dependent nodes as one operation.** Introduce a shared run record that pins the input version and transform for each step. Connect a filtered or grouped table to two consumers, change one source row, and let the user preview and run the affected steps together. An unavailable column should block only the steps that require it. After the graph closes mid-run, reopening should show which results were committed and which steps can resume. This takes C1 into the durable execution work in C2.

## 1. The product we are building

Graphlink should not become another chat interface with nodes drawn around the messages. Its graph should be the **actual substrate on which intelligence and work happen**:

- **Explore:** Decompose a question into parallel lines of reasoning; use different models, data, tools, and agents on each branch.
- **Execute:** Connect nodes into real, typed, reusable workflows. A connection should be able to carry data and trigger downstream work, not merely draw a line.
- **Act:** Let capable agents research, write code, inspect repositories, manipulate files, run tests, analyze data, and operate approved external tools.
- **Remember:** Give the system deep, selective, workspace-aware context instead of repeatedly dumping entire chat histories into a model.
- **Create:** Produce and edit documents, structured data, charts, code, images, audio, video, and combinations of them in the same canvas.
- **Inspect and control:** Make every run understandable: inputs, model choice, tool calls, approvals, intermediate outputs, retries, errors, costs, and resulting artifacts.
- **Improve:** Compare models and strategies with repeatable evaluations and reuse successful workflows.

**North-star experience:** A user gives Graphlink a complex objective. It constructs an inspectable plan, splits independent work among bounded agents, searches the user's project and permitted external sources, invokes appropriate models and tools, creates usable artifacts, verifies them, presents alternatives, and lets the user steer, branch, rerun, or combine the results. The whole process remains a graph the user can edit and understand.

This is a capability roadmap. Completion means **working features with behavior proven by tests and real end-to-end tasks**, not “build an installer and see whether people like it.”

## 2. Start from the real codebase — do not rebuild what already exists

| Existing foundation | Current reality | What is still missing |
| --- | --- | --- |
| Canvas and graph model | React Flow canvas; Python `SceneDocument`; node/edge roles, branching, comparison, synthesis, undo/redo, grouping, graph search, incremental scene updates. [`backend/domain/`](backend/domain) · [`SceneCanvas.tsx`](web_ui/src/app/canvas/SceneCanvas.tsx) | **Typed input/output ports, executable dependency edges, subworkflow composition, dependency invalidation, and first-class run inspection.** |
| Agent execution | Builder has plans, Autopilot/Co-pilot, budgets, approvals, checkpointable plan state. Workspace Agent has file/shell/Python tools, a durable JSONL transcript and compaction. [`builder.py`](backend/builder.py) · [`harness/`](backend/harness) | **A general workflow runtime, parallel task scheduling, durable in-flight jobs, shared artifacts, and coordinated agent teams.** |
| Subagents | `subagent.spawn` exists, but its worker is read-only, depth-one, and returns a summary to the parent. [`subagents.py`](backend/harness/subagents.py) | **Parallel delegation, explicit roles, structured results, task ownership, safe write-capable workers, and graph-visible coordination.** |
| Models | Ollama, llama.cpp, 26 named cloud profiles and a custom endpoint, model pins, task overrides, some capability-aware routing. [`README.md`](README.md) · [`api_provider.py`](api_provider.py) | **Concurrent use of independent cloud profiles, reliable provider feature normalization, graph-wide model comparisons, robust fallbacks and measured routing.** |
| Knowledge and research | Local file ingestion, FTS5 + vector hybrid retrieval, workspace-scoped collections, graph indexing, web research with source metadata. [`knowledge_retrieval.py`](backend/knowledge_retrieval.py) · [`knowledge_store.py`](backend/knowledge_store.py) | **A deliberate context assembly engine, reranking, source-change tracking, repository-aware retrieval, browser interaction and evidence-aware synthesis.** |
| Developer and external tools | Scoped `ToolRegistry`; Gitlink; Review Lens; virtual environment runner; MCP stdio client; limited headless MCP server; plugin SDK and optional worker process. [`tools.py`](backend/tools.py) · [`plugin_sdk.py`](backend/plugin_sdk.py) | **More complete agent tools, secure remote MCP, declarative plugin UIs, stronger isolation, structured tool artifacts, and dependable long-running operations.** |
| Creation | Artifact/Drafter, chart nodes, HTML preview, and Fal Media Studio with priced/approved generation. [`docs/ARTIFACT_DRAFTER_REDESIGN.md`](docs/ARTIFACT_DRAFTER_REDESIGN.md) · [`docs/FAL_MEDIA_STUDIO.md`](docs/FAL_MEDIA_STUDIO.md) | **Connected multimodal pipelines, first-class data/table assets, document production, model-to-model transfers, and media analysis/editing on the graph.** |
| Evaluation | Extensive unit tests, an offline Builder/chart/structured-output evaluation harness, some browser and performance coverage. [`backend/evals/`](backend/evals) · [`backend/tests/perf/`](backend/tests/perf) | **End-to-end agent task evaluations, adversarial tool tests, execution determinism, long-run resilience, quality/cost benchmarks and regression tracking.** |

**Important architectural distinctions:**

1. The current graph is already a useful **conversation/visualization graph**. A new **execution graph** must coexist with its existing structural-parent, reference, and association semantics. Do not reinterpret every current edge as a dependency.
2. A Builder plan can resume, and a Harness transcript persists. That does **not** equal durable, independently schedulable workflow jobs that reliably survive disconnections/restarts and avoid repeating side effects.
3. There is already an MCP client **and** a scoped Graphlink MCP server. Extend their capabilities; do not pitch “add MCP” as a new feature.
4. There is already a plugin SDK and out-of-process workers. Those workers have resource limits and an environment allowlist, **not** a general OS-enforced filesystem/network sandbox.
5. The current “best quality” auto-model policy uses **known price as a proxy**. Do not present it as an actual quality benchmark. [`graphlink_model_catalog.py`](graphlink_model_catalog.py)

## 3. Impact-based priority

Priority is about how much genuine capability a workstream unlocks, not how visible or easy it is to demo. Dependencies matter more than arbitrary dates.

| ID | Priority | Workstream | Why it matters | Depends on |
| --- | --- | --- | --- | --- |
| **C1** | **P0** | Executable, typed graph | Turns visual connections into composable work | Additive graph contracts |
| **C2** | **P0** | Durable execution/orchestration kernel | Makes multi-step autonomy dependable | Run/step/artifact identity |
| **C3** | **P0** | Advanced context and memory | Lets agents work on large, long-lived projects intelligently | Retrieval + provenance |
| **C4** | **P0** | True multi-model runtime | Allows local/cloud, per-node and multi-model operation simultaneously | Independent provider profiles |
| **C5** | **P0** | Powerful agent teams and delegation | Upgrades single-agent loops into complex problem-solving | C2, C3, C4 |
| **C6** | **P1** | Deep research + interactive browser | Gives agents real access to complex current information | C2, C3, permission gates |
| **C7** | **P1** | Full repository and software-engineering agent | Turns code-related nodes into a capable development environment | C2, C3, tools |
| **C8** | **P1** | Data, document and artifact engine | Makes outputs useful beyond chat text | C1, C2, asset contracts |
| **C9** | **P1** | Connected multimodal workflows | Unifies image/video/audio/model pipelines | C1, C2, media assets |
| **C10** | **P1** | MCP/plugin/extension platform v2 | Allows capabilities to grow without core bloat | C2, security boundaries |
| **C11** | **P1** | Large-graph UX and execution debugger | Makes complex work navigable, explainable and controllable | C1, C2 |
| **C12** | **P0, ongoing** | Safety, reliability, evaluations, observability | Makes every other capability safe enough to trust | Starts immediately |
| **C13** | **P2** | Graph intelligence and autonomous experimentation | Differentiates Graphlink after the execution foundation exists | C1–C5 |
| **C14** | **P2, conditional** | Multi-user collaboration and sync | Useful later; expensive and distracting before core runtime works | Stable history/data model |

The **first major engineering bet** is **C1 + C2 + C3 + C4**. Those four multiply the value of nearly every existing and proposed feature. Research, coding, media and agents should plug into those same foundations rather than each growing its own unrelated run loop.

---

## 4. P0 foundations: the largest capability jumps

### C1 — Turn the graph into an executable, typed system

**Objective:** Any compatible node should be able to receive structured inputs from another node, perform a task, and publish outputs that downstream nodes can consume. A user can build a workflow visually and an agent can build the same workflow through scoped graph tools.

**Build:**

- Add **named, typed input/output ports** to executable nodes. Start with `text`, `json`, `table-ref`, `file-ref`, `image-ref`, `audio-ref`, `video-ref`, `node-ref` and `collection-ref`. Preserve type/version metadata and distinguish a secret reference from secret text.
- Introduce a distinct **dataflow edge** and **control/dependency edge**, separate from existing conversation ancestry and annotation/reference edges. The visual affordance must make these meanings obvious.
- Define a **canonical execution-node contract**: input schema, output schema, capabilities, deterministic configuration, side-effect classification, version, and validation rules. Existing chat, research, artifact, chart, code, plugin and Fal nodes can gain adapters incrementally.
- Add useful execution primitives: **Input/Parameter**, **Model**, **Tool**, **Transform**, **Condition**, **Merge**, **Map/Batch**, **Approval**, **Evaluate**, and **Output**. Do not create cosmetic duplicate cards when an existing kind can implement the contract.
- Support **nested/reusable subgraphs** with an exposed input/output interface, versioned parameters and copy/reuse semantics. A user should be able to collapse a complete research or data-analysis pipeline into one reusable workflow node.
- Validate ports on connect and before execution; reject missing required inputs, incompatible types, invalid references, unbounded cycles, and unauthorized actions. Provide explicit conversion nodes instead of silently coercing arbitrary data.
- Separate **graph definition** from **graph run**. Moving a node or editing a note must not accidentally re-execute a costly workflow. Runs start explicitly, or via opt-in triggers defined in a later phase.
- Track **which input artifact versions** each output was generated from. When upstream inputs change, mark dependents stale and offer selective rerun. Do not destroy previous results merely because an input changed.
- Support **bounded loops** only through an explicit Loop/Iteration primitive with stop conditions and limits. Keep the ordinary dependency graph acyclic for simple topological scheduling.
- Make workflow definitions serializable and portable without embedding active credentials or executable Python objects.

**Implementation seams:** [`backend/domain/model.py`](backend/domain/model.py), [`backend/domain/graph.py`](backend/domain/graph.py), [`backend/domain/structure.py`](backend/domain/structure.py), [`backend/domain/commands.py`](backend/domain/commands.py), [`contracts/`](contracts), [`backend/tools_graph.py`](backend/tools_graph.py), [`SceneCanvas.tsx`](web_ui/src/app/canvas/SceneCanvas.tsx), and save/load adapters. Prefer additive schemas and explicit migrations over changing the meaning of legacy edges.

**Acceptance gate:** A user constructs `CSV input → transform → chart → analysis → document` on the canvas. Values actually flow between nodes; every port validates; a bad transformation blocks only its dependents; saving/loading preserves exact configuration; undo/redo works; a changed CSV marks only the correct downstream nodes stale.

### C2 — Build a durable execution and orchestration kernel

**Objective:** Graphlink can execute, pause, resume, inspect and cancel complex graph workflows and agent tasks without depending on a single WebSocket session or a long-lived in-memory coroutine.

**Build:**

- Introduce explicit durable entities: **Run**, **StepRun**, **Attempt**, **ApprovalRequest**, **ArtifactVersion**, **ToolInvocation**, and **RunEvent**. Use immutable run IDs and stable references to graph/workspace state.
- Add a **persisted run journal and checkpoint store** with monotonic sequence numbers. Status should distinguish `queued`, `running`, `awaiting-approval`, `paused`, `cancelling`, `cancelled`, `failed`, `completed` and `reconciliation-required`.
- Evolve—not discard—[`RunRegistry`](backend/run_lifecycle.py), Builder's plan checkpointing, and Harness's [transcript](backend/harness/transcript.py). Make them clients of one lifecycle contract. Keep their existing cancellation and approval invariants during migration.
- Schedule graph steps by dependencies. Permit bounded **parallel independent steps**, multiple concurrent runs, run-specific priorities and cancellation; do not keep “only one of each kind per session” as the ultimate concurrency model.
- Stream **run events and artifact references**, not full growing transcripts in every scene update. Support disconnect/reconnect from the last acknowledged event sequence.
- Add **pause/resume**, cooperative cancellation, deadlines, per-step retries with jitter/backoff, provider rate limits, and concurrency ceilings. Separate retryable read-only failures from uncertain external writes.
- Require an **idempotency key or reconciliation protocol** for actions with side effects (including paid media submissions and file mutations). Never interpret “the HTTP request timed out” as proof the remote job did not start.
- On restart, recover committed checkpoints; identify incomplete attempts; **reconcile** rather than blindly repeat an uncertain operation. Approval never becomes a reusable authorization merely because a run was resumed.
- Attribute costs and token usage to runs/steps/models and enforce **pre-call budgets** where possible, with clear “estimate unavailable” behavior when the provider does not expose pricing.
- Give every run a **structured, exportable trace**: input versions, model/tool selection, approval decision, output artifact version, fault classification, attempt history and timing. Redact credentials and avoid storing private payloads in diagnostic logs.

**Implementation seams:** [`backend/run_lifecycle.py`](backend/run_lifecycle.py), [`backend/builder.py`](backend/builder.py), [`backend/harness/loop.py`](backend/harness/loop.py), [`backend/agent_dispatch/`](backend/agent_dispatch), [`backend/events.py`](backend/events.py), new `backend/workflow/` and `backend/run_store.py` modules, generated contracts and an execution panel.

**Acceptance gate:** A 12-step workflow with two independent branches is interrupted at several points: model streaming, a pending approval, a tool result, a crash, and an uncertain paid submission. Recovery preserves completed outputs, asks again where human consent is required, resumes eligible work, and **never duplicates a completed side effect**. Two independent runs advance concurrently without one stealing the other's callbacks or state.

### C3 — Give Graphlink an intelligent context and memory engine

**Objective:** Models receive the **right** information, in the **right** amount, with traceable origins. Long workspaces should not become slower, more expensive or less coherent merely because they contain thousands of nodes or large files.

**Build:**

- Introduce a reusable **ContextAssembler** used by chat, Builder, Harness, subagents, research and workflow model nodes. Each call selects relevant ancestors, graph neighborhoods, user-pinned notes, knowledge hits, artifact excerpts, tool summaries and task instructions under a **model-specific token budget**.
- Retain existing FTS5/vector retrieval and add **reranking, query decomposition, source deduplication, parent/section retrieval, recency controls, and graph-neighborhood relevance**. Measure improvements before replacing a working retrieval path.
- Add **code-aware indexing**: repository file tree, symbols, references, imports, tests and changed files. Retrieve function/class spans rather than massive raw files. Use incremental reindexing after edits.
- Provide **workspace memory** consisting of user-approved durable facts, decisions, conventions, project structure and prior outcomes. Keep factual memory distinct from chat history, generated guesses and untrusted web content.
- Implement **hierarchical summaries** for long branches and long agent traces. Preserve source IDs and important decisions, mark summaries as summaries, and retain a way to request original evidence.
- Support **context pinning/exclusion** per branch or agent. Show a **Context Inspector**: what will be sent, where it came from, how many tokens it costs, which source is stale, and whether it leaves the device.
- Track source/content revisions; update or invalidate embeddings, summary fragments, memories and derived outputs when their inputs change. Do not silently serve old source material as current.
- Keep local-only processing enforceable: embeddings and summaries must not silently select a cloud model when the workspace or step forbids network inference.
- Make retrieval/tool results **lower trust** than user and system instructions. Defend against prompt injection through typed source wrappers, explicit trust labels, allowed-tool checks, and model-visible citations rather than prompt wording alone.

**Implementation seams:** [`backend/knowledge_*.py`](backend), [`backend/api/intents_knowledge.py`](backend/api/intents_knowledge.py), [`graphlink_memory.py`](graphlink_memory.py), [`backend/harness/context.py`](backend/harness/context.py), [`backend/domain/branches.py`](backend/domain/branches.py), [`backend/agents.py`](backend/agents.py).

**Acceptance gate:** With a large project and many prior branches, an agent can answer a question requiring two distant code symbols and an earlier decision, identify its exact sources, and stay inside its selected model's context window. Changing a source invalidates the affected retrieval/memory entries. Tests demonstrate that instructions inside a retrieved document cannot authorize tools.

### C4 — Make simultaneous multi-model execution first-class

**Objective:** The user can use multiple local and cloud models **at the same time**, assign them to individual nodes/agents, compare them, and route work based on real capabilities and measured task results.

**Build:**

- Refactor provider state toward an **independent connection/profile registry**. Each profile owns its base URL, credential reference, model catalog, limits, availability and feature map. No request may accidentally inherit the active global provider's credentials.
- Allow an Ollama model, a configured local GGUF and two different cloud providers to execute **concurrently** on different branches without requiring the user to switch the active endpoint.
- Normalize model capabilities across providers: streaming, tool calls, structured output, vision, audio, image, reasoning, context length, rate limits and compatible message/attachment formats. Unknown capability must be represented as unknown—not guessed from model name.
- Strengthen streaming and tool-call compatibility, including provider reasoning/tool-turn requirements, cancellations, schema validation and error normalization. Build contract fixtures for each supported protocol family.
- Add **fan-out comparison** from one selected context to N models, side-by-side results, consistent input hashes and easy branch creation from each result. Support blind ranking, rubric scoring and follow-up comparison.
- Replace the “more expensive means better” quality heuristic with **evidence from task-specific evaluations**. Keep fastest/cheapest policies as explicit alternatives; never silently reroute a user-pinned model.
- Add opt-in fallbacks, retry ceilings, circuit breaking and per-connection quotas; let the user specify whether a failure may cross from local to cloud. Make actual model identity and usage visible on every generated artifact.
- Provide a **routing matrix** for users to choose a model for reasoning, planning, code edits, retrieval, embeddings, vision, speech, summarization and media. Automatic selection is allowed only within declared privacy, capability and spending constraints.

**Implementation seams:** [`api_provider.py`](api_provider.py), [`provider_runtime/`](provider_runtime), [`backend/providers/`](backend/providers), [`graphlink_model_catalog.py`](graphlink_model_catalog.py), [`graphlink_cloud_providers.py`](graphlink_cloud_providers.py), [`backend/settings.py`](backend/settings.py), [`web_ui/src/app/chrome/SettingsDialog.tsx`](web_ui/src/app/chrome/SettingsDialog.tsx).

**Acceptance gate:** The same frozen prompt plus cited context runs in parallel on Ollama, Provider A and Provider B; each request uses the correct credentials, response format, model and cancellation channel. Model A failing does not abort B. Local-only mode makes **zero** outbound inference calls.

### C5 — Evolve Builder and Workspace Agent into a coordinated multi-agent system

**Objective:** Complex tasks can be decomposed, delegated, independently investigated, verified and synthesized. Humans can interrupt or redirect work without losing useful results.

**Build:**

- Extend the existing **depth-one, read-only summary subagent** into a bounded task system: worker role, goal, input artifact references, required output schema, permitted tools, budget, deadline, parent run and explicit result.
- Support **parallel research/review workers** and constrained write-capable workers only in isolated workspaces/worktrees. Workers may return structured artifacts, patches, citations or test results—not just an unstructured paragraph.
- Add optional agent roles: **Planner**, **Researcher**, **Coder**, **Data Analyst**, **Critic/Verifier**, and **Synthesizer**. Roles are configurable tools/capabilities, not separate ungoverned AI personalities.
- Make delegation and reconciliation **visible in the graph**: task nodes, owner/status, dependencies, failures and outputs; a parent can inspect a worker's trace on demand without flooding its own context.
- Implement a **review/verification stage** for consequential outputs: run tests, check cited evidence, validate JSON/schema, compare expected and actual file diffs, surface contradictions and request correction where justified.
- Support re-planning after failed tools, partial results and new evidence, while preserving user-approved constraints and already-completed work.
- Add a **human control surface** for asking follow-ups mid-run, overriding a plan, skipping a step, pausing, denying an action, or accepting one alternative branch as the path forward.
- Bound fan-out, recursion, token/tool cost and side-effect scopes; never allow an agent team to recursively spawn an unlimited swarm or escalate its own permissions.

**Implementation seams:** [`backend/builder.py`](backend/builder.py), [`backend/harness/subagents.py`](backend/harness/subagents.py), [`backend/harness/loop.py`](backend/harness/loop.py), [`backend/tools.py`](backend/tools.py), [`backend/agent_dispatch/`](backend/agent_dispatch), new task/delegation orchestration in C2.

**Acceptance gate:** One goal launches two parallel read-only investigators, a critic that checks both sets of findings, and a synthesizer that builds an editable report. Each task is independently cancellable, uses a constrained model/tools budget, and leaves an inspectable trace. A failed investigator does not erase the other result; the parent reports the incomplete branch rather than inventing a conclusion.

---

## 5. P1: give agents substantially more useful abilities

### C6 — Deep research, evidence handling and browser automation

Current Web Research already searches, fetches, extracts and synthesizes sources. Extend it into a serious investigation tool:

- Add a **browser-backed research tool** for JavaScript-rendered sites, pagination, link following, document downloads and interactive pages, with an isolated browser profile. Browsing and external account actions must be permission-scoped.
- Support **multi-query research plans**, alternate search strategies, source deduplication, extraction from PDFs and structured pages, source reliability signals, publication/retrieval timestamps and contradiction discovery.
- Introduce **Evidence/Claim** records: source URL or local reference, excerpt/offset, capture hash, source timestamp and associated claim. Show what supports, contradicts or fails to support a generated conclusion.
- Make research outputs usable as **typed workflow inputs**: a source collection, an evidence table and a structured summary can feed charts, Agent nodes, Artifact nodes and other research branches.
- Support **follow-up research** over an existing set of sources, freshness re-checks and comparison of two research runs. Keep source snapshots or cached excerpts where allowed and identify unavailable pages instead of silently rewriting history.
- Make source trust and fetch security explicit: SSRF defenses, redirect limits, size/time budgets, rate limits, blocked private networks, isolated cookies, prompt-injection labeling and download approvals.

**Vertical slice:** “Investigate why two sources disagree” creates parallel search branches, retrieves supporting excerpts, checks dates, and produces a report with supporting and opposing evidence that can be inspected back to its source. No invented citations.

### C7 — Make the coding agent exceptionally capable

Existing Gitlink, Review Lens, shell/Python tools and Workspace Agent are foundations, not the finished coding experience.

- Build **repository intelligence**: incremental symbol indexing, definitions/references, dependency graph, test discovery, relevant-file retrieval and project instructions.
- Support **multi-file edits** as reviewable patch sets with snapshots, per-file diffs, staged acceptance, safe apply/rollback and conflict handling. Reuse Gitlink's selective apply and existing approval/undo concepts.
- Give coding workflows tools for **search → plan → edit → lint/typecheck → run targeted tests → inspect errors → repair → summarize**. Attach command output and changed files as run artifacts.
- Introduce **isolated git worktrees or scratch copies** for parallel code workers so two agents cannot corrupt one another's edits. Require explicit merge/review into a user's project.
- Add repo-aware **test selection** and test-failure analysis, not blind reruns of the entire suite after every edit. Use execution limits and a readable command disclosure.
- Make relevant external tooling callable through the same permission system: Git, issue/PR inspection, build logs, local terminals, databases and eventually controlled browser/computer tools.
- Allow an agent to **reason over a repository visually**: file/symbol node references, task-to-change edges and test-to-change provenance.

**Vertical slice:** From a bug report in a local repository, Graphlink identifies affected symbols, creates a multi-file patch, runs the relevant tests, shows the diff and failures, repairs once, and offers a reviewed apply. Denial, cancellation and stale-base conflicts leave the original checkout intact.

### C8 — Build first-class data, documents and artifact production

- Add **Table/Dataframe** and **Dataset** assets with schema, row count, source/version and bounded preview. Support CSV/TSV/JSON/Parquet and selected SQLite data; add other formats when a clear workflow needs them.
- Build graph-connected operations for **filter, join, aggregate, transform, validate, plot and explain**. A model should generate a proposed transform that a deterministic engine can validate and run—not quietly invent numerical results.
- Support **SQL query nodes** and Python analysis in isolated, bounded execution contexts. Differentiate read-only queries from mutations and expose connection scope/approvals.
- Extend charts so their **data source is a versioned artifact**, not merely an answer rendered from prose. Drill from chart point to the rows or computation that produced it.
- Continue the existing Artifact/Drafter redesign with **document editing, section-targeted AI proposals, revision history, tracked diffs, structured outlines and exports** (Markdown, HTML, PDF, DOCX) as useful *creation features*, not distribution work.
- Add a **visual artifact inspector**: side-by-side source/preview, table editor, file tree, rendered Markdown, HTML preview, chart editor and attachment provenance according to artifact type.
- Support batch import and **folder ingestion** with file watching/change detection, user-selected scopes, duplicate handling and incremental reindexing. Scanned documents can use OCR through an explicit, separately tested import path.

**Vertical slice:** Drop two CSVs and a specification into a workspace; the user constructs or asks an agent to construct a reproducible transform, a chart and a report. Editing one source row marks the right results stale; recomputation changes the chart and document with a visible diff.

### C9 — Make multimodal workflows genuinely composable

Fal Media Studio already has model discovery, input schemas, price review, queueing and output playback. The next step is **multimodal orchestration**, not another disconnected generation dialog.

- Treat **image, audio, video, document and 3D outputs** as typed, versioned assets that any compatible model/tool node can receive.
- Add **analysis as well as generation**: visual question answering, image editing/segmentation, OCR, audio transcription, audio event extraction, video frame/timeline analysis, subtitles, text-to-speech and audio transformations where supported.
- Connect arbitrary compatible media steps: **prompt → storyboard → image → edit → voiceover → video → captions**, with schema-aware field mapping and explicit transfer of uploaded/returned assets.
- Expand the Studio's UI into a **media input/output inspector** for files, previews, metadata, versions, provenance, comparative outputs and drag-to-connect ports. Keep large bytes out of graph snapshots and model context unless requested.
- Handle asynchronous jobs as first-class C2 runs with persistent remote IDs, status, cancellation, failed-job recovery, quotes/estimates, and separate authorization for each paid operation.
- Permit local media models where supported, so cloud media APIs are an option rather than an architectural assumption.

**Vertical slice:** A three-model media chain transfers one model's actual image output into another model's edit input and then into a video request. All artifacts survive restart, each paid request is independently approved and an interrupted queue submission cannot silently duplicate a billable job.

### C10 — Upgrade MCP and plugins into a real capability platform

- Extend the **existing MCP client** from local stdio to standards-compatible secure remote transport, discovery, authentication, session lifecycle, retries, timeouts, per-tool grants and response constraints. Keep local and remote secrets isolated.
- Expand the **existing Graphlink MCP server** beyond limited live-canvas writes with safe saved-graph operations, artifact discovery, explicit run start/status/cancel and approval handoffs. Do not expose an unrestricted `dispatch_intent` or an approval-bypass tool.
- Evolve the plugin SDK to expose **typed ports, tool specs, structured artifacts, lifecycle hooks, schema-driven forms and node inspectors**. A plugin should be able to add an actual workflow-capable component without patching core React code.
- Support **declarative plugin UI descriptors** first; any custom web renderer must be isolated by a reviewed sandbox/CSP/IPC model rather than arbitrary HTML/JS embedded into the trusted application.
- Improve plugin **activation, updates, permissions, revocation, crash recovery and worker supervision**. Move from coarse install grants toward explicit per-capability enforcement.
- Treat untrusted plugins as untrusted processes. Resource limits and environment scrubbing alone do not prevent filesystem or network access; plan OS-specific sandbox isolation or clearly constrain the trust model.
- Publish a **small reference SDK and conformance suite** inside the repository, covering one UI node, one model tool, one persisted custom state and one long-running tool.
- Add integrations only when they unlock a capability (e.g. repo hosting, data sources, local apps, browser tools). Do not inflate the provider/plugin count for its own sake.

**Vertical slice:** A third-party plugin declares typed inputs, renders a settings form, executes a bounded tool, emits a file artifact, survives save/reload and can be revoked mid-run with no access to Graphlink's provider secrets.

### C11 — Build a canvas that remains powerful at real scale

Existing React Flow, LOD behavior, lazy node components, routing and patches are strengths. Scale and interaction must grow with the execution engine.

- Add synchronized **Canvas**, **Outline/List**, **Execution Timeline**, **Compare**, and **Artifact Detail** views over the same underlying graph. The list and timeline are alternate views, not duplicate stores.
- Introduce **semantic zoom**, clustering, search-to-focus, progressive node rendering, large-graph layout, saved views and filters by status, node kind, model, workflow and owner.
- Give complex nodes a **real inspector/workspace** so long documents, code diffs and media timelines are not forced into tiny canvas cards.
- Add **workflow debugging**: per-node run state, queue position, input/output preview, step-through, breakpoint/pause-before-step, retry, skip where safe, and cause-of-failure navigation.
- Add keyboard-first operations: quick node creation, connect ports, multi-select actions, duplicate subgraph, convert to reusable workflow, compare selected nodes, collapse completed work and jump between errors.
- Keep wire traffic **incremental** during streaming and execution; avoid replacing large scene snapshots on every tool token. Benchmark rendering, memory, selection, moving grouped nodes and route recomputation on genuinely large fixtures.
- Provide useful AI assistance inside the canvas: “explain this subgraph,” “identify the broken dependency,” “summarize selected branches,” and “suggest the next node,” with results previewed before edits.

**Vertical slice:** A thousands-node workspace remains interactive during two streaming agent runs; the user can search for a failed step, inspect the failing input and retry only its descendants without losing viewport state.

---

## 6. C12 — Engineering invariants required for every capability

These are **development requirements**, not an instruction to stop building features and work on packaging.

### Security and permissions

- All model-originated tool use passes through the same **server-side** permission checks as user-originated operations. A model, plugin, WebSocket client or MCP adapter must not get a private backdoor.
- Treat files, web pages, repository comments, tool outputs and model messages as **untrusted data**. Test prompt-injection attempts against realistic tool sequences and require a permission boundary, not a reassuring system prompt.
- Distinguish **read**, **local mutation**, **external write**, **code execution**, **filesystem escape**, and **billable provider action**. Default to minimal grants; show the exact target, side effects and costs when approval is required.
- Preserve **fresh explicit approval** for Python execution and paid actions. No approval replay after restart or argument mutation. Cap execution resources and terminate process trees.
- Replace plaintext saved-secret fallbacks with a **fail-closed or explicitly session-only storage policy** where practical, using OS credential stores on macOS/Linux and existing DPAPI on Windows. This is runtime capability/security work, **not** a packaging milestone.
- Isolate browser sessions, remote MCP, provider credentials, plugin workers and code sandboxes. Add SSRF, path traversal, symlink, redirect, archive bomb and response-size tests at their actual boundaries.

### Reliability and architecture

- Preserve old workspaces through **additive migrations**; never silently reclassify a chat/annotation edge as dataflow. Keep schema/codegen drift checks and domain invariants.
- Use **typed reference handles** for artifacts rather than inline large binary payloads. Maintain content hashes, bounded storage and cleanup rules.
- Keep side-effect-bearing calls **idempotent or explicitly reconcilable**. No “retry everything” policy.
- Avoid new monoliths. Separate workflow runtime, context assembly, provider registry, artifact service, and UI views into modules with contracts; incrementally extract only the relevant sections of existing large files.
- Make error states actionable: the offending node, failed dependency, model/tool name, constraint, retry eligibility and preserved outputs should be inspectable.

### Evaluations and observability

- Expand [`backend/evals/`](backend/evals) from deterministic chart/structured-output/Builder tests into **task-level agent evaluations**: multi-step coding, source-grounded research, context retrieval, data accuracy, model routing, workflow execution and tool safety.
- Maintain two kinds of tests: **offline deterministic contracts** in CI and **opt-in live model quality comparisons** with recorded model/version/price. Do not mistake a scripted model's correct tool choice for proven autonomous reasoning.
- Add **fault injection**: process death, disconnect, stale graph revision, duplicate external response, slow plugin, corrupt transcript, exhausted budget, malicious retrieved text, denied approval and partial artifact write.
- Store **privacy-conscious run traces** with input/output hashes, timings, usage, retries and error categories. Raw content export should be explicit and scrubbed. No background cloud telemetry by default.
- Define a benchmark suite for medium and large graph workflows; track p50/p95 interaction latency, memory, token cost, success rate and wrong-side-effect count. Use measured baselines rather than invented claims.
- Make “**zero unauthorized actions, zero duplicate paid actions, zero silent data loss**” blocking invariants throughout development.

Every feature PR must include: user-visible behavior, a clear contract change, migration/compatibility consideration, negative/permission tests, cancellation/restart tests if it executes work, and a short proof that the intended real workflow operates end-to-end.

---

## 7. P2: differentiating intelligence once the core works

### C13 — Make the graph itself an intelligent collaborator

These should be built **after** C1–C5, not before:

- **Automated hypothesis branching:** Ask the system to propose competing approaches with assumptions, tests, expected evidence and measurable success criteria. Run bounded experiments in parallel.
- **Cross-branch reasoning:** Compare arguments, evidence, model outputs and artifacts; detect contradictions, duplicate investigations, missing assumptions and places where two branches can be merged.
- **What-if and sensitivity analysis:** Change an input or model and preview which outputs would be affected before running anything. Compare runs at the artifact, node and claim level.
- **Graph-aware natural language commands:** “Find the chain that produced this chart,” “rerun just the failing tests,” “compare the two approaches,” or “turn these nodes into a reusable workflow.” Translate into a previewable structured plan; never execute an ambiguous destructive command implicitly.
- **Workflow optimization:** Recommend batching, caching, parallelizable steps, cheaper proven models and redundant-tool removal using recorded traces. The model can propose optimizations; deterministic checks establish whether the transformed graph preserves required behavior.
- **Continuous verification:** Configurable assertions attached to nodes or edges (“schema valid,” “all claims cited,” “tests pass,” “cost under limit”). Failed assertions become visible blockers and can route to a repair step.

### C14 — Collaboration and sync (only after the data model is stable)

If and when needed, plan versioned graph changes, shared comments/reviews, conflict detection, scoped workspace access and eventual collaboration. Do **not** build real-time co-editing or cloud account infrastructure at the expense of local execution, agent power or dataflow first. A local-first share/export remains useful without a synchronization backend.

---

## 8. Dependency-driven development phases

**No calendar promises.** Each phase is complete when the actual capability gate passes. More than one team may advance independent tracks.

| Phase | Engineering focus | What must work at exit |
| --- | --- | --- |
| **Phase A — Contracts and runtime spine** | Typed graph ports/edges; artifact references; durable run/step/event schema; migration tests; permission model alignment | One persisted 3-node dataflow works; its run is inspectable and restorable; old chats still load unchanged |
| **Phase B — Execution intelligence** | Dependency scheduler, parallel runs, checkpoints/recovery, ContextAssembler, independent provider profiles | A multi-branch agent workflow can run concurrently, recover from interruption, use two models and stay inside a stated context/cost budget |
| **Phase C — Agents and serious tools** | Parallel structured subagents, browser research, repo intelligence, code edit/test loop, source-linked knowledge | A research task and a code task each complete a real, reviewable multi-step workflow with safe tool use and reproducible artifacts |
| **Phase D — Creator workflows** | Table/data nodes, advanced documents, multimedia ports, chained Fal/local media models, visual debugger | A mixed text/data/media pipeline transfers typed results correctly and supports selective reruns, inspection and controlled spending |
| **Phase E — Advanced autonomy and extensions** | Plugin SDK v2, remote MCP, graph-native hypothesis experiments, reusable templates, evaluation-driven improvements | An external plugin participates as a safe workflow step; complex scenarios branch/compare/reconcile without hidden actions |

**Crosscutting work from day one:** C12 permission boundaries, fault injection, deterministic tests, task-level evaluations, migration safety and observability. “P0 ongoing” does not mean delaying the P0 capabilities until every theoretical hardening item is done.

## 9. The first 15 implementation PRs

These are deliberately **small, independently reviewable capability slices**, not broad rewrite branches. Split further if a PR changes too many interfaces.

| Order | Proposed change | Primary areas | Proof of completion |
| --- | --- | --- | --- |
| **1** | **Execution-graph RFC + contracts**: specify port types, edge semantics, artifact references, run states and migrations | `backend/domain/`, `contracts/`, design doc | Old graph fixtures still mean exactly what they meant; new definitions have schema tests |
| **2** | **Persist typed ports and data edges** with frontend connection validation | `backend/domain/`, `backend/session_*`, `SceneCanvas.tsx` | Connect/save/reload/undo typed data edges; reject incompatible ports |
| **3** | **Artifact reference service** for versioned JSON/text/table/file/media values | `backend/asset_store.py`, new artifact module, contracts | Nodes exchange handles; binary data never floods WS scene updates |
| **4** | **Durable run/step journal** with event sequence and recovery API | `backend/run_lifecycle.py`, new `run_store.py`, `backend/events.py` | Kill/restart retains completed steps and a queryable run history |
| **5** | **Minimal dataflow executor** (Input → Model/Transform → Output) | new `backend/workflow/`, `backend/tools_graph.py` | Correct dependency order, input validation, cancellation, output versioning |
| **6** | **Execution inspector UI** (state, trace, inputs, outputs, errors, approvals) | `web_ui/src/app/`, generated contracts | Inspect and resume a run without showing raw private prompts by default |
| **7** | **Independent provider profiles and parallel routing** | `api_provider.py`, `provider_runtime/`, settings | Two different cloud credentials and local Ollama run simultaneously |
| **8** | **ContextAssembler v1 with inspector** for branch/graph/knowledge selection | `backend/knowledge_*`, `backend/harness/context.py`, UI | Token-bounded context pack; visible source selection; no local-to-cloud leak |
| **9** | **Parallel read-only subagent tasks** with typed outputs and bounded budgets | `backend/harness/subagents.py`, run kernel | Two independent workers run and return evidence without context contamination |
| **10** | **Workflow Condition/Merge/Approval nodes** and selective stale/rerun | workflow engine, domain, UI | Changed upstream input reruns only dependent nodes; approvals still gate actions |
| **11** | **Codebase symbol index + structured file search** | `backend/harness/tools_fs.py`, knowledge/code indexing | Query finds symbol definitions/references with exact file/line origins |
| **12** | **Reviewable multi-file code-edit/test workflow** in isolated worktree | `graphlink_plugins/gitlink/`, Harness tools, workflow | Generate diff, test, repair and explicitly apply or discard |
| **13** | **Evidence-bearing research and synthesis adapters** | `graphlink_plugins/web_research/`, `backend/api/intents_branches.py` | Research/document evidence enters a synthesis through typed references |
| **14** | **Table/data nodes and transforms** backed by versioned artifacts | domain, data tools, `ChartNodeView` | CSV → transformation → chart is real, reproducible dataflow |
| **15** | **Workflow-capable plugin + MCP conformance slice** | `backend/plugin_sdk.py`, `backend/mcp_client.py`, control API | Third-party typed tool works, persists, obeys grants and terminates on revoke |

After PR 5, the first real graph workflow is possible. After PR 10, Graphlink has a useful orchestration engine. After PR 15, it has a foundation other agents, data tools, media steps and external extensions can share. Media pipelines, browser automation and advanced agent roles can proceed on adjacent feature branches once their dependencies are available.

## 10. Feature ideas worth building — but not before the foundations

**High-upside expansions:**

- **Browser/computer agent:** browser navigation, DOM/screenshot inspection, download handling and approved form actions using isolated sessions. Avoid uncontrolled desktop interaction.
- **Visual coding workflows:** code-to-test-to-review pipelines, repository architecture visualization and CI failure diagnosis.
- **Model laboratory:** prompt/parameter sweeps, A/B comparisons, ensemble voting, artifact scoring and measurable local-vs-cloud tradeoffs.
- **Research laboratory:** source graphs, fact checks, contradiction maps, repeatable searches and evidence-change detection.
- **Media studio:** multi-stage video/audio/image production with schema-driven connections, previewable asset versions and cost gates.
- **Data laboratory:** structured datasets, SQL, Python notebooks as graph steps, interactive charts, validation and reproducible reports.
- **Reusable agent/workflow library:** configurable role profiles, prompt versions, typed workflow templates, shared run fixtures and simulation before execution.
- **Graph analysis:** explain dependency chains, detect unreachable nodes, visualize cost hotspots, identify duplicate work and suggest parallel execution.

**Explicitly out of scope for this roadmap:** desktop installers, release channels, marketing, onboarding conversion funnels, growth measurements, social features, mandatory cloud accounts, billing plans, cosmetic-only redesigns and adding dozens of integrations simply to increase a count. Those can have separate plans when relevant; they are **not substitutes for making the software more capable**.

## 11. Definition of “extremely capable”

Graphlink reaches the intended level when these behaviors are routine rather than demonstrations:

1. A visual graph is a **real executable workflow**, including typed data, validation, conditions, parallelism, reruns and composable subgraphs.
2. An agent can pursue a **large multi-step objective**, delegate bounded parallel work, use appropriate tools, recover from interruption and produce reviewable artifacts.
3. Context comes from the **workspace, codebase, documents, graph and prior work** without flooding the model or crossing declared privacy boundaries.
4. Different local and cloud models can **work together concurrently**; performance, costs and capability limitations are observable and compared fairly.
5. Research, coding, data, document and media workflows are **first-class capabilities** on the same canvas, not disconnected single-use cards.
6. A user can **inspect, pause, redirect, debug, compare, and selectively rerun** any consequential part of a workflow.
7. New capabilities can be introduced through **safe tools, MCP and plugins** without undermining the core permission model.
8. The features withstand **real failures**—disconnects, invalid model outputs, stale data, denied approvals, process death and tool errors—without hidden actions, lost work or duplicate spending.

**Do next:** land the typed graph contracts, durable run model and first executable graph slice. In parallel, prototype independent provider profiles and the shared context assembler. Then expand agent power and tool depth on top of that spine. That is the path to a tool whose capabilities are fundamentally greater than “AI chat on a canvas.”
