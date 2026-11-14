# Graphlink roadmap

**Make a branching investigation produce a decision another person can verify.** Start with engineers and analysts who need to explain a result to a colleague. Graphlink should let them test competing explanations, show which evidence changed their mind, and revise the conclusion when new data arrives. Engineering incident reviews give this work a concrete first audience and a repeatable test.

This proposal is based on [`main` at `bf0fa15`](https://github.com/dovvnloading/Graphlink/commit/bf0fa15414190912b62a8efe909df08613c4d96d), reviewed November 14, 2026. The milestone order reflects dependencies. The targets below are proposed release criteria. Collect baselines in the first iteration before setting dates or claiming improvements.

## The first investigation to build

Use the README’s latency incident. Give an engineer a deploy note and a small CSV of request timings. They fork database and application hypotheses, attach evidence for each, and produce a chart and a short decision brief. A second engineer opens the handoff offline, finds the rows behind the chart, and explains why one hypothesis was rejected. Then a corrected CSV arrives. The reviewer can see which findings need another look, rerun the affected work, and compare both decisions.

That last step gives the canvas a purpose beyond arranging answers. Google’s Gemini Notebook lets a reader jump to a cited passage. Langflow exposes the inputs, outputs, and timings of a component run. ChainForge can score responses across models and settings. Graphlink can bring those expectations into a single investigation whose evidence, alternatives, and revisions travel together. [Gemini Notebook citations](https://support.google.com/gemininotebook/answer/16179559) · [Langflow traces](https://docs.langflow.org/traces) · [ChainForge evaluations](https://chainforge.ai/docs/)

## Where development should concentrate

| Area | What ships today | Highest-value change |
| --- | --- | --- |
| Investigation | Compare and Synthesize accept selected chat histories. Synthesis saves the source node IDs, instructions, provider, and model. Web research holds source hashes, and indexed document chunks retain exact offsets. [Branch selection](backend/api/intents_branches.py) · [Synthesis metadata](backend/domain/branches.py) · [Research sources](web_ui/src/app/canvas/WebResearchNodeView.tsx) · [Document offsets](backend/knowledge_chunking.py) | Connect a claim in a finished brief to the exact evidence the author used. |
| Distribution and trust | CI builds a wheel with the frontend. The documented first install involves Python, Node, and native dependencies. At review time, the GitHub Releases list was empty. Saved credentials can fall back to plaintext. [Install guide](docs/PLATFORM_GUI_INSTALL.md) · [CI](.github/workflows/ci.yml) · [Releases](https://github.com/dovvnloading/Graphlink/releases) · [Credential storage](graphlink_secrets.py) | Give a new user one tested download and a first investigation they can open without an account or key. |
| Handoff and reuse | The versioned workspace archive carries chats and assets. The local knowledge index lives separately. Builder recipes save a goal, step titles, and mode, and interrupted builds can resume. [Archive format](backend/workspace_archive.py) · [Recipes](settings_store/recipes.py) · [Builder](README.md#the-builder-and-the-workspace-agent) | Package the cited evidence and preserve enough run history to repeat selected steps. |
| Model choice | Graphlink lists 26 named cloud providers alongside its local routes. A cloud-pinned branch needs that provider active. The “best quality” routing policy uses the highest known token price as its proxy. [Provider setup](README.md#api-providers) · [Pinned dispatch](api_provider.py) · [Routing policy](graphlink_model_catalog.py) | Compare configured models on the same evidence and choose by measured task quality, latency, and cost. |

## Milestone 1 · Ship a usable first investigation

Run two pieces of work in parallel. One removes installation friction. The other tests whether the proposed decision brief is worth building.

- **Release the desktop app.** Start on Windows, the primary development platform. Publish a signed installer and checksum built from a tagged commit. Exercise install, native window launch, upgrade, backup restoration, and reopening an older workspace. Qualify macOS and Linux separately on clean machines with their native webviews. [Platform guide](docs/PLATFORM_GUI_INSTALL.md) · [Existing backup support](backend/db_backup.py)
- **Protect saved credentials.** Keep Windows DPAPI, add macOS Keychain and Linux Secret Service, and offer session-only or environment-supplied credentials when secure storage is unavailable. Migrate a legacy value only after a verified write. Cover an interrupted migration and locked keyring, and check settings, exports, and diagnostics for the test secret.
- **Ship the smallest evidence workflow.** Build a key-free sample from the deploy note and CSV, with two competing branches and an example brief. Add a working path from one sentence to one versioned source excerpt. Observe first-time users install, open the sample, find the evidence, and identify what would change the decision.

**Exit criteria.** On a clean Windows machine, 8 of 10 first-time participants reach the correct supporting excerpt within five minutes of starting the install, without coaching. Every credential migration and upgrade fixture passes before publishing the installer. Record where participants stop, even if they finish. Apply the same native-launch, upgrade, and secure-storage checks to each additional OS before advertising support for its installer.

## Milestone 2 · Make conclusions verifiable and portable

1. Extend synthesis to selected research results, local passages, notes, and chart inputs. Show the inputs and context budget before sending. In the resulting editable brief, distinguish supported findings, conflicting evidence, and open questions. Let the author correct a citation without regenerating the document.
2. Save each evidence reference with a stable ID, content digest, source revision, and locator such as a text span or a CSV row and column. Record which inputs a chart or conclusion actually used. A changed source should flag dependent findings and preserve the prior version for inspection.
3. Extend `.graphlink` export with a preview of included branches and excerpts. Let the author omit sensitive material and show an explicit missing-source marker to the recipient. Carry the brief and selected evidence into a clean workspace with an empty knowledge index. Keep older archives readable and imports inert. [Existing archive guarantees](backend/workspace_archive.py)

**Exit criteria.** Use fixtures with agreeing, conflicting, corrected, and deliberately withheld evidence. Every included reference resolves to the same passage or rows after save, restart, and offline import. Every withheld reference is visibly unavailable. Separately, have two reviewers independently score model-authored claims against their cited passages. Target at least 90% supported factual claims on a held-out set, and require every seeded contradiction to remain visible in the brief.

## Milestone 3 · Rerun only the work that changed

Give a Builder recipe named inputs, expected outputs, and a version. Start with the incident workflow. For each completed step, save its input versions, output references, model and settings, tool version, and observed usage where available. Show the steps affected by a replacement CSV, preview the estimated cost, and let the user select what to rerun. Keep both runs and show the change to the conclusion.

Extend the existing budget, approval, and resume machinery. A retry must reconcile completed work before repeating a tool call. An uncertain external job should show its status for review. Keep fresh approval for Python execution and use an isolated, resource-limited runner for shared recipes that process data. [Builder evals](backend/evals/README.md) · [Python execution behavior](docs/PYTHON_EXEC_APPROVAL.md) · [Recipe storage](settings_store/recipes.py)

**Exit criteria.** Replacing the CSV reruns the chart and its dependent synthesis once. The unrelated hypothesis stays untouched. Cancelling, restarting, or losing a provider response preserves the original brief, respects the run budget, and never duplicates a paid job. Opening a shared recipe runs zero steps until the recipient starts it.

## Milestone 4 · Make model choice measurable

Allow two configured cloud profiles and a local model to answer the same frozen context without changing the workspace default. Bind each credential to its endpoint, show exactly which evidence leaves the device, and honor a local-only choice throughout. Present results side by side with model names initially hidden, a task-specific scoring rubric, latency, and actual or explicitly estimated cost.

Use reviewed scores to inform the quality policy. Display the sample count, task type, and date. Set the minimum sample size before evaluating candidates and leave quality unrated below it. A provider failure should leave the other results available. Work on comparison can start once credential isolation and versioned evidence land, independently of recipe replay.

**Exit criteria.** A lower-priced model that earns a better score on held-out tasks wins the quality recommendation. An unrated model stays visibly unrated. Contract tests verify equal input evidence, isolated credentials, independent cancellation, and zero external requests in local-only mode.

## How to decide what ships next

| Measure | Method and proposed decision rule |
| --- | --- |
| Can another person verify the decision faster? | Give at least 12 reviewers matched incidents in Graphlink and linear chat. Alternate task order and hold evidence and model constant. Score the chosen hypothesis, supporting rows, and rejected explanation blind. Target 30% lower median review time with equal or better accuracy. If accuracy falls, fix the brief before expanding recipe coverage. |
| Does the canvas stay usable as evidence accumulates? | Extend the existing 2,000-node backend fixture to desktop measurements on a named mid-range machine. Publish cold-open time and p95 edit-to-paint and search-to-focus latency while a reply streams. Begin with p95 edit-to-paint under 100 ms. Profile the slow interaction before changing the renderer or index. [Existing backend baseline](backend/tests/perf/baseline.json) |
| Is the workflow valuable a second time? | In an opt-in pilot, count how many of 10 authors return within two weeks to revise a real brief or reuse its recipe. Aim for 5. Interview people who stop and record the exact step they abandoned. Keep collection local or accept volunteered observations. |

**First three work items.** Prepare two versions of the incident CSV and the answer rubric. Exercise the release candidate on a clean Windows image. Wire one sentence in the sample brief through save, restart, and click-through to its exact source. Those deliverables let design, release engineering, and backend work proceed independently in the first iteration.

**Defer until there is evidence of demand.** Pilot asynchronous handoff before funding real-time collaboration. Consider live co-editing when several pilot teams repeatedly need simultaneous edits to the same investigation. Expand the provider catalog or launch a plugin marketplace when pilots can name a recurring task that existing models, recipes, and plugins cannot complete.
