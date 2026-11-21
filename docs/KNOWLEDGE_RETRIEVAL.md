# Local knowledge retrieval: ranking, citations and offline evaluation

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
only when saving settings or rendering the UI. Turning the choice Off
clears that consent and stops future embedding requests.

The page's **Test** button embeds a fixed synthetic phrase, not any
stored document. A live saved embedding choice backfills missing vectors
on demand; vector caches are keyed by local model or cloud
(provider, endpoint, model) identity, so switching models never compares
incompatible vector spaces. If an enabled service fails at retrieval
time, search falls back to local keyword-only results and logs the
failure. Workspaces remain collection-scoped.

## Pipeline

`backend.knowledge_retrieval.hybrid_search()` retains the existing FTS5
BM25 and optional vector/cosine retrieval. FTS5 is always available. A vector
query runs only when the caller supplies an embedding-capable provider and a
model id; the reranker itself makes **no embedding, LLM, network or cloud
calls**. Offline evaluation uses only an in-process, deterministic embedding
stub and a temporary SQLite database.

The previous behavior is reproducible with `ranking="legacy"`: request the
first `k` FTS5 hits and first `k` vector hits, perform reciprocal rank fusion
(RRF, `k_rrf=60`), and return the first `k` fused chunks. Without embeddings,
the previous behavior returns FTS5 results alone. No previous citation fields
or offsets are removed.

The new default (`ranking="reranked"`) proceeds as follows:

1. Ask each available index for `min(4*k, 100)` candidates, but never fewer
   than `k`. Keep the independently ranked results; BM25 and cosine scores
   have incompatible units and are **not** compared.
2. Fuse lists using `RRF_score = Σ 1/(60 + rank)`, with 1-based ranks and
   per-list `chunk_id` deduplication. Agreement between indexes boosts a
   hit. Stable first-seen order breaks score ties.
3. Walk the fused ranking, keeping its highest-ranked representative and
   discarding subsequent exact or near-duplicate chunks. Unicode word tokens
   are case-folded; substantially overlapping ordered three-token shingles
   trigger a more conservative ordered sequence-similarity comparison. Very
   short passages are removed only when exactly equal. This is a lexical
   similarity filter, **not** a claim of semantic equivalence. Continue
   examining the over-fetched pool to fill up to `k` unique passages.
4. Read provenance in one local SQLite query and attach it to every surviving
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
`rankSignals` and `rrfScore` while preserving all existing keys. Global
search reuses this same formatter. Existing `[kN]` untrusted-context markers
remain for compatibility; the stable `kb:` id is included alongside them.

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
The existing model-tool serializer (`backend/tools_knowledge.py`) exposes a
subset of result fields; the enhanced result contract is available at the
retrieval layer and knowledge/global-search intents without changing that
out-of-scope serializer.
