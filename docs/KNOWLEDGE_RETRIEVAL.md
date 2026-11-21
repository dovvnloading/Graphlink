# Local knowledge retrieval: ranking, citations and offline evaluation

For the shipped user-facing workflows, see the [Knowledge Library guide](KNOWLEDGE_LIBRARY.md). This page describes the retrieval implementation, citation metadata and reproducible evaluation.

## Settings > Knowledge

The Knowledge page chooses embedding retrieval independently of the chat
model. **Off (keyword search)** is the default and uses local FTS5 BM25 +
reranking without contacting an embedding endpoint. **Ollama (local)**
discovers models installed on the running Ollama daemon, verifies each
model's reported `embedding` capability, sorts supported models first,
and recommends `nomic-embed-text` only when installed. The selected model
is saved in the normal `session.dat` settings store.

**Cloud (OpenAI-compatible)** reuses a configured Settings > API Endpoint
connection and an explicitly entered embedding-model ID. The user must
check the consent control explaining that **document text leaves the
machine**: indexed passages and search queries will be transmitted to
that service. The backend checks the persisted consent on every use, not
only when saving settings or rendering the UI. Consent is bound to the
**actual embedding API endpoint URL**, not just its provider name. Changing
the endpoint or loading legacy opt-in settings without this binding requires
a fresh explicit opt-in. Cloud endpoints must use HTTPS, except HTTP to
local loopback addresses. Turning the choice Off clears consent and stops
subsequent requests; an already-started request cannot be recalled.

The embedding endpoint receives extracted text chunks (at most 32 per
batch) and semantic search queries, not original file bytes or paths.
An initial cold workspace search can send one batch of up to 32 chunks.
This choice does **not** control cloud chat-model egress: enabling Knowledge
in a chat with a cloud model can separately send cited passages to its
provider even if embeddings are Off.

The page's **Test** button embeds a fixed synthetic phrase, not any
stored document. A saved choice starts a **background embedding job** for
the current workspace. File/folder imports, branch indexing, saved graphs
(including autosave) and retained Web Research sources also queue their
originating workspace after ingestion.
The job checks the pending cache, sends batches of at most **32 chunks**,
and commits each batch independently. Cancellation is cooperative at batch
boundaries (including after an in-flight provider response), so already
committed batches survive a cancellation, failure or restart. A resume job
queries only the remaining chunks. Switching models cancels an old run and
starts one for the newly selected embedding space; selecting Off stops new
embedding calls. All provider, SQLite and chunk work runs off the event
loop. Cached vectors are keyed by local model or cloud
(provider, endpoint, model) identity, so switching models never compares
incompatible vector spaces.

The backend publishes the `knowledge-embedding` topic:
`{jobs: [{jobId, workspaceId, source, model, modelId, status, chunksTotal,
chunksEmbedded, chunksRemaining, cancelRequested, error}]}`. Statuses are
`queued`, `running`, `completed`, `cancelled` and `failed`.
`knowledge/getEmbeddingJobs` returns the snapshot;
`knowledge/cancelEmbedding(jobId)` requests cancellation; and
`knowledge/resumeEmbedding()` resumes pending chunks in the *current*
workspace. `knowledge/refreshEmbeddings()` handles a saved settings change.
Cloud provider/SDK failures are logged, but the topic exposes a fixed,
redacted error rather than exception text that might contain credentials or
document contents. The shipped Knowledge Library dialog shows **file-import**
progress and **watched-folder** sync status, but does not yet expose this
separate embedding-job topic or its cancel/resume controls.

Searches normally use completed vector batches and FTS5 while background
jobs fill the cache. To preserve the existing UI, chat, and agent hybrid
search behavior for small cold workspaces, the first workspace-scoped search
can embed **one batch of at most 32 chunks** if that entire collection has
no cached vectors for the selected model. Larger or partially embedded
collections are cache-only on search; their full indexing remains a
cancellable background job. The cold-start batch runs off the event loop
and checks for settings changes before committing vectors.

If an enabled service fails at retrieval time, search falls back to local
keyword-only results and logs the failure. Workspaces remain
collection-scoped.

## Shipped entry points and scope

The top-bar **Knowledge** button opens the **Knowledge Library** dialog for
the current workspace. **Add files** and **Add folder** use native pickers;
drag-and-drop files are staged as private local upload copies. These are
one-time imports, not folder subscriptions. Session-owned `knowledge-ingest`
jobs report progress, per-file outcomes and cancellation, and trigger a
background embedding run after successful new/updated content. The
**Documents** table offers **Open** (indexed text reconstructed from stored
chunks in the docked Document View), **Re-index** (recorded file sources
only) and confirmed **Remove** (deletes indexed data, not the original file).
Document View is not an OS-level file opener, and source formatting or
separators lost during chunking are not guaranteed to be reproduced.

**Add watched folder** is a separate, explicit opt-in via the native picker.
The app-lifetime watcher scans on startup and roughly every ten minutes;
**Sync now** and **Sync all now** schedule additional scans. A successful,
complete scan reconciles disappeared tracked files, preserving content
still backed by another watched folder/source; unreadable or cancelled
scans do not prune missing files. Removing a watch leaves existing indexed
documents intact. Watch synchronization uses its **own** ingestion manager
and status, not the session's `knowledge-ingest` topic, and currently does
**not** itself enqueue an embedding job. Its keyword index is searchable
immediately; semantic vectors may be populated later by a settings refresh
or eligible cold-start search. See the user guide for the folder exclusions,
limits and status shown in the dialog.

The chat composer has a separate **Use knowledge** switch. Without a branch
override, it follows whether the current workspace has documents; explicit
branch choices inherit along descendants, not across sibling branches. When
on, ordinary chat Send, Regenerate and model comparisons search only that
workspace and budget retrieved passages into the outgoing model context.
Turning it off does not affect explicit Library searches. A hosted chat model
can still receive selected knowledge passages even with local-only keyword
or Ollama embedding search.

Both Builder and the workspace Agent can call read-only, automatically
permitted `knowledge.search` (`query`, optional `k` default 5 / max 25).
It requires an existing current workspace collection and returns cited,
untrusted excerpts without accepting a model-supplied collection ID. Only
the workspace Agent can call `knowledge.add` (`path` within its bound
workspace); each write requires fresh human approval, including in modes
that auto-approve ordinary file edits. The source file is not modified.

## Filesystem and archive boundaries

Source files are opened with a bounded, no-follow descriptor reader and
parsed from a private snapshot, preserving the protections added in #906.
Watched-folder scans additionally pin the root's device and inode for the
whole import, including before database writes and missing-file pruning,
so a different directory at the same pathname cannot silently inherit
the original watch authorization.

Knowledge imports limit DOCX and XLSX ZIP archives to 2,048 members,
32 MiB total expanded bytes and 4 MiB per XML member before the parser
allocates a document tree. Existing document XML element/entity checks
and PDF protections remain in effect. On POSIX, the dedicated knowledge
directory and SQLite files are secured before opening; hardlinked database
and WAL/SHM files are rejected so other paths cannot share knowledge data.
Legacy retrieval context encodes every source-controlled field, including
citation IDs, as JSON strings so embedded newlines cannot spoof extra
citation blocks or top-level instructions.

## Pipeline

`backend.knowledge_retrieval.hybrid_search()` retains the existing FTS5
BM25 and optional vector/cosine retrieval. FTS5 is always available. A vector
query runs only when the caller supplies an embedding-capable provider and a
model id; the reranker itself makes **no embedding, LLM, network or cloud
calls**. Offline evaluation uses only an in-process, deterministic embedding
stub and a temporary SQLite database.

The legacy ranking order remains available with `ranking="legacy"`: request
the first `k` FTS5 hits and first `k` vector hits, apply the semantic-only
relevance floor, perform reciprocal rank fusion (RRF, `k_rrf=60`), and return
the first `k` fused chunks. Without embeddings, FTS5 results are returned
alone. No previous citation fields or offsets are removed.

The new default (`ranking="reranked"`) proceeds as follows:

1. Ask each available index for `min(4*k, 100)` candidates, but never fewer
   than `k`. Keep the independently ranked results; BM25 and cosine scores
   have incompatible units and are **not** compared.
2. Remove vector-only candidates with cosine similarity below **0.55**
   (`MIN_SEMANTIC_COSINE_SIMILARITY`). FTS5 hits survive irrespective of their
   vector score, while strong paraphrase matches without keyword overlap
   remain eligible. This is an absolute floor within the vector index, not
   a comparison between BM25 and cosine values; it applies to both rankings.
3. Fuse lists using `RRF_score = Σ 1/(60 + rank)`, with 1-based ranks and
   per-list `chunk_id` deduplication. Agreement between indexes boosts a
   hit. Stable first-seen order breaks score ties.
4. Walk the fused ranking, keeping its highest-ranked representative and
   discarding subsequent exact or near-duplicate chunks. Unicode word tokens
   are case-folded; substantially overlapping ordered three-token shingles
   trigger a more conservative ordered sequence-similarity comparison. Very
   short passages are removed only when exactly equal. This is a lexical
   similarity filter, **not** a claim of semantic equivalence. Continue
   examining the over-fetched pool to fill up to `k` unique passages.
5. Read provenance in one local SQLite query and attach it to every surviving
   hit. With no vector index, FTS5-only results are still deduplicated and
   citeable. `rank_signals` identifies the first-stage FTS5/vector ranks;
   `rrf_score` is available when both lists are present.

When less than `k` unique candidates exist, the response legitimately
contains fewer than `k` rows. Both modes honor `collection_id`; neither
searches across a workspace boundary set by the caller.

## Citations

Every `hybrid_search()` hit includes:

| Field | Meaning |
|---|---|
| `source_file` | Original `documents.source_uri` (filesystem path, URL or synthetic `graph:` / `branch:` locator; it is not necessarily a local file) |
| `section` | Nearest Markdown ATX (`#`) or setext (`===`/`---`) heading at or before the chunk's start offset; `Unsectioned` if none exists |
| `citation_id` | Stable `kb:` prefix plus the first 20 hex characters of SHA-256 of `source_uri`, `source_node_id` when present, `offset_start`, `offset_end` and exact chunk text, separated by a unit separator |
| `offset_start`, `offset_end` | Original character offsets for the evidence (unchanged) |
| `rank_signals` | Per-index 1-based candidate ranks in the new ranking |

IDs do not depend on SQLite `chunk_id`/`document_id`, the search query or
ranking position. Deleting/reingesting the same evidence with the same source
and offsets produces the same citation. Editing source text, changing its
location, or shifting offsets intentionally changes it. IDs are evidence
locators, **not authorization tokens** or proof that the text is true.

Sections are computed from existing stored chunk text and absolute offsets,
including indexes created before this feature. No schema migration or
reingestion is necessary. Chunks containing content from multiple sections
are attributed to the section active at their *start*; formats without
Markdown headings report `Unsectioned` rather than guessing. The knowledge
WebSocket result formatter adds `sourceFile`, `section`, `citationId`,
`rankSignals` and `rrfScore` while preserving all existing keys. The Knowledge Library's search results display the
per-hit signals as **Keyword #rank** (FTS5), **Semantic #rank** (vector),
or both, including on collapsed result cards; an absent signal is never
shown as a match. These are per-index candidate **ranks**, not comparable
raw scores or confidence measures. Expanding a Library result shows the
exact indexed passage and its character offsets; only an HTTP(S) source
gets an external **Open source** link. Global search reuses the same
result formatter. Existing `[kN]` untrusted-context markers remain for
compatibility; the stable `kb:` id is included alongside them.

For ordinary chat, the backend promotes only model-written `[kb:...]`
citations that match passages **actually selected for that reply's prompt**.
A server-authored provenance ledger (stored separately from the model's
text) supplies the trusted source identity. Verified entries appear as
**Sources** chips on the assistant card and call `knowledge/openCitation`
with the document ID, chunk ID and citation ID, verified again against the
current workspace. Document View opens the reconstructed indexed text and
highlights the cited passage. Invented, missing or stale citation IDs cannot
create clickable sources; they are shown as unverified instead. The Library
**Documents → Open** action does not require a citation and opens the same
reader without a citation highlight.

## Reproducible offline evaluation

From the repository root, with backend dependencies installed:

```bash
python -m backend.evals.knowledge_retrieval --assert-improved
python -m pytest -q backend/tests/test_knowledge_retrieval.py backend/tests/test_knowledge_retrieval_rerank.py backend/tests/test_knowledge_retrieval_eval.py backend/tests/test_knowledge_store.py backend/tests/test_intents_knowledge.py
```

The benchmark fixture is
`backend/evals/fixtures/knowledge_retrieval.json`: 14 sample Markdown
documents (six separately archived near-duplicate helpdesk indexes, six
source-of-truth procedures and two unrelated documents) and six realistic
questions, each with explicit relevant source file(s). The index pages
repeat the questions but link to, rather than contain, the actual procedure.

Every run creates and destroys a temporary SQLite FTS5/vector database,
uses the normal ingestion and vector-search functions, and runs the **same
questions and gold sources** under `ranking="legacy"` and
`ranking="reranked"`. The provider uses deterministic, in-process word
concept groups with normalized vectors. This intentionally tests the
reranking algorithm under complementary lexical/semantic signals; it is
**not** a measured score for a trained embedding model or a production
corpus. It requires no downloaded weights or service and makes no remote
requests. To reproduce, invoke the benchmark directly, not an API.

**Metrics:** Recall@5 averages, across questions, the fraction of relevant
source files appearing in the top five. MRR is the mean reciprocal rank of
the first relevant source in the returned top five (MRR@5; a miss scores 0).
Repeated chunks from a source do not inflate Recall@5. The gate accepts the
new ranking only if neither metric regresses and at least one improves.
The automated test asserts that this gate still holds when the code or
fixtures change.

Checked-in fixture results, obtained with the local deterministic provider:

| Ranking | Recall@5 | MRR@5 |
|---|---:|---:|
| Legacy top-k RRF | 0.8333 | 0.1667 |
| RRF + overfetch + near-duplicate suppression | **1.0000** | **0.4444** |

The reranked mode is therefore the default. To compare another corpus,
replace the fixture and re-run the gate; if either metric regresses, change
`DEFAULT_RANKING` back to `"legacy"` until the regression is addressed.
Both modes remain selectable without feature flags or schema changes.

## Configured-model quality benchmark (manual only)

To measure retrieval with the **actual embedding model chosen in
Settings > Knowledge**, run this separately from the deterministic gate,
from the repository root:

```bash
python -m backend.evals.knowledge_model_benchmark --run
```

The command requires an explicitly enabled Ollama or consented
OpenAI-compatible cloud embedding choice. It uses the *same checked-in
14-document, six-question fixture* as the offline reranker evaluation and
creates a temporary SQLite database; it never reads or modifies your
workspace knowledge index. It measures keyword-only FTS5 + default reranking
against hybrid FTS5/vector + the **same** default reranking. The keyword
baseline makes no embedding calls. Fixture passages are embedded once with
the configured model; each hybrid question then uses that model's query
embedding. Provider failures abort rather than silently reporting fallback
keyword results as hybrid.

Its JSON output identifies the model and includes both modes' average
**recall@5** and **MRR** (truncated to the top five, i.e. MRR@5), plus
per-question scores and returned source files. Scores are fractions of
relevant sources retrieved and the reciprocal rank of the first relevant
source, respectively. No score threshold is asserted: even when hybrid
performs worse, the command only reports the measurements and exits
successfully if the run completed.

This is an opt-in **local invocation**, not an automated regression test.
It requires `--run`, is absent from the normal eval runner and PR
workflows, and refuses to run when `CI` or `GITHUB_ACTIONS`
is set. If a consented cloud embedding model is selected, the **fixture
passages and questions leave the machine** and provider charges may apply;
Ollama keeps embedding requests local. Scores reflect a small synthetic
corpus and your current model configuration, not production retrieval
performance.

## Limitations

The evaluation dataset is intentionally small and a deterministic stand-in
for an actual embedding model. It proves an improvement on these fixtures,
not universal retrieval quality. Headings are inferred from available
Markdown in stored chunks and may be absent for PDFs or plain-text sources.
The model-tool serializer (`backend/tools_knowledge.py`) also exposes
`rank_signals` and `rrf_score` alongside source and citation fields, so
Builder and workspace agents see the same match provenance as panel searches.


## Scale and accessibility audit: 5,000 documents / 200,000 chunks

Run the deterministic, offline scale benchmark from the repository root:

```sh
python -m backend.tests.perf.measure_knowledge_library
```

It builds a **disposable** SQLite corpus of 5,000 documents and 200,000
FTS5-indexed, embedded chunks. It reports document-list, lexical search,
exact vector top-10, verified citation-opening latency, plus 3,200-chunk
embedding and 200-file ingestion throughput samples. The fake provider
measures local pipeline throughput, not model inference or network calls.
Times depend on the host CPU, disk and SQLite build; they are observations,
not hard, flaky CI timing thresholds.

The library renders 50 document rows per page rather than mounting 5,000
sets of action controls. Vector retrieval remains an **exact flat scan**:
it streams batches of compact stored vectors, keeps a bounded top-k heap,
and fetches full passage text only for winners. Computation and SQLite
I/O still scale linearly with the number of embeddings (this is not an
approximate-nearest-neighbor index), while peak memory no longer grows
with all chunk text and vectors. The embedding background job pins a
count/high-water mark and loads only the next missing batch; folder ingest
reuses its SQLite connection while keeping independent per-file commits
and rollbacks.

Knowledge search cards can open a server-verified citation in the existing
Document View at the indexed passage; external source links remain separate.
Document-list pagination, search status and destructive-action
confirmations have keyboard controls and screen-reader announcements.
The side reader becomes inert when closed and restores focus on exit.
