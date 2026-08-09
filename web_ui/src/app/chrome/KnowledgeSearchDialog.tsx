import { useRef, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { Dialog } from "../overlays/overlays";

/**
 * ADR-017 stage 17.5: the "Knowledge" search panel - a human-driven,
 * request/reply front end for backend/api/intents_knowledge.py's own
 * `knowledge/search` intent (backend.knowledge_retrieval.hybrid_search()
 * underneath - lexical FTS5 always, fused with vector search whenever an
 * embedding provider/model is configured, degrading gracefully to
 * lexical-only otherwise). Distinct from backend/tools_knowledge.py's
 * ToolRegistry-registered `knowledge.search` tool - that one is for a
 * future ADR-008 model-driven tool call inside a live conversation; this
 * one is for a person searching their own ingested knowledge base
 * directly, mirroring DiagnosticsDialog's own transport.request()
 * "I need the actual return value" pattern (not fireIntent, which is
 * `: void`).
 *
 * "N sources used" (the ADR's own stage-17.5 exit criterion phrasing):
 * the result count heading below. "Opens the cited source at the right
 * offset": each result IS already the exact passage recorded at
 * `document[offsetStart:offsetEnd]` (backend.knowledge_chunking's own
 * offset-exactness contract) - expanding a card reveals that precise
 * span verbatim, and a source whose `sourceUri` is a real http(s) URL
 * (a web-research-retained page) gets a genuine "Open source" link. A
 * local file path has no OS-level "jump to this byte offset" mechanism
 * anywhere in this codebase (verified before building this - see this
 * ADR's own stage 17.5 recon) - this component does not fabricate one;
 * the offset is shown as citation metadata and the excerpt itself IS the
 * content at that offset, which is the honest, buildable version of the
 * exit criterion's own claim.
 */

interface KnowledgeSearchResult {
  chunkId: number;
  documentId: number;
  documentTitle: string;
  sourceUri: string;
  text: string;
  offsetStart: number;
  offsetEnd: number;
}

function isHttpUrl(value: string): boolean {
  return /^https?:\/\//i.test(value);
}

function KnowledgeResultCard({ result }: { result: KnowledgeSearchResult }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <li className="knowledge-result-item">
      <button
        type="button"
        className="knowledge-result-header"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="knowledge-result-title">{result.documentTitle}</span>
        <span className="knowledge-result-source">{result.sourceUri}</span>
      </button>
      {expanded && (
        <div className="knowledge-result-body">
          <p className="knowledge-result-excerpt">{result.text}</p>
          <div className="knowledge-result-footer">
            <span className="knowledge-result-offset">
              Offset {result.offsetStart}–{result.offsetEnd}
            </span>
            {isHttpUrl(result.sourceUri) && (
              <a
                className="knowledge-result-open-link"
                href={result.sourceUri}
                target="_blank"
                rel="noreferrer"
              >
                Open source
              </a>
            )}
          </div>
        </div>
      )}
    </li>
  );
}

export function KnowledgeSearchDialog({ transport }: { transport: WsTransport }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KnowledgeSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Adversarial-review finding: vector search embeds the query via a
  // network round-trip, so latency is not correlated with send order - a
  // second search fired while the first is still pending could otherwise
  // have its response arrive AFTER a later request's and silently
  // overwrite fresher results (or a slow failure could hide a fast
  // success). A monotonically increasing sequence token, checked before
  // ever touching state in a resolved/rejected handler, makes only the
  // MOST RECENTLY SENT request's own response ever win - matches the
  // "ignore stale in-flight responses" pattern this codebase already uses
  // elsewhere for the same reason (e.g. WsTransport's own per-request id
  // correlation).
  const latestRequestId = useRef(0);

  function runSearch() {
    const trimmed = query.trim();
    if (!trimmed || searching) return;
    const requestId = ++latestRequestId.current;
    setSearching(true);
    setError(null);
    transport
      .request("knowledge", "search", [trimmed, 10])
      .then((value) => {
        if (requestId !== latestRequestId.current) return; // a newer search has since been sent
        const payload = value as { results: KnowledgeSearchResult[] };
        setResults(payload.results);
      })
      .catch(() => {
        if (requestId !== latestRequestId.current) return;
        setError("Search failed - see graphlink.log for details.");
      })
      .finally(() => {
        if (requestId === latestRequestId.current) setSearching(false);
      });
  }

  return (
    <Dialog name="knowledge" title="Knowledge" className="knowledge-search-dialog">
      <div className="knowledge-search-row">
        <input
          type="text"
          className="knowledge-search-input"
          aria-label="Search the knowledge base"
          placeholder="Search your ingested knowledge base…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") runSearch();
          }}
        />
        <button
          type="button"
          className="knowledge-search-button"
          onClick={runSearch}
          disabled={searching || !query.trim()}
        >
          {searching ? "Searching…" : "Search"}
        </button>
      </div>

      {error && (
        <p className="knowledge-empty" role="alert">
          {error}
        </p>
      )}

      {results != null && !error && (
        <>
          <h2 className="knowledge-section-title">
            {results.length === 0 ? "No sources found" : `${results.length} source${results.length === 1 ? "" : "s"} used`}
          </h2>
          <ul className="knowledge-result-list">
            {results.map((result) => (
              <KnowledgeResultCard key={result.chunkId} result={result} />
            ))}
          </ul>
        </>
      )}
    </Dialog>
  );
}
