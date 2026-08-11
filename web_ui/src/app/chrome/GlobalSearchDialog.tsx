import { useReactFlow } from "@xyflow/react";
import { useRef, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { motionDuration } from "../reducedMotion";
import { Dialog, useOverlays } from "../overlays/overlays";

/**
 * ADR-020 stage 20.4: the "Global Search" dialog - search across every
 * workspace's graphs AND every ingested knowledge document at once
 * (backend's new "search"/"globalSearch" intent, backend.knowledge_store's
 * real chunks_fts/chunks/documents underneath - collection_id=None, the
 * same "no filter, search everything" mechanism search_chunks/hybrid_search
 * already support, not a second cross-collection union built for this
 * stage). A direct sibling of KnowledgeSearchDialog.tsx (ADR-017 stage
 * 17.5) - same request/reply shape, same monotonic-requestId
 * stale-response guard (a real adversarial-review-driven fix there, for
 * exactly the same "a network round trip can resolve out of send order"
 * race a global search hits too), same expand-a-card-for-the-cited-excerpt
 * result-card interaction. This file does not invent a different
 * result-list shape - see that component for the pattern this one mirrors.
 *
 * The one real difference: a hit can come from TWO kinds of source now,
 * not one. A document hit (sourceNodeId/graphId both null - an ordinary
 * ingested file or web-research page, same shape KnowledgeSearchDialog
 * already renders) keeps that dialog's own "Open source" external-link
 * behavior verbatim - KnowledgeSearchDialog.tsx itself is untouched by
 * this stage. A graph/node hit (both non-null - knowledge_store's
 * chunks.source_node_id column, this stage's own migration 5) instead gets
 * a "Jump to node" action: transport.request(...) for the new
 * loadGraphAndFocusNode intent resolves with the node's real {x, y} - the
 * backend has already fully restored that graph into canvas_document by
 * the time this promise resolves, so there is no separate "scene" topic
 * race to wait out (see that intent's own backend docstring) - then
 * useReactFlow().setCenter, the exact same call SearchOverlay.tsx's own
 * jumpTo() already makes for an in-canvas match, just fed coordinates from
 * the reply instead of a locally-computed one.
 *
 * Wire contract note (frontend/backend built concurrently against ADR-020
 * stage 20.4's shared design doc, no live handshake): this file calls
 * transport.request("globalSearch", "search", [query, k]) and
 * transport.request("app-chat-library", "loadGraphAndFocusNode",
 * [graphId, nodeId]) - the exact topic/intent names and result field names
 * (chunkId/documentId/documentTitle/sourceUri/text/offsetStart/offsetEnd,
 * plus this stage's own sourceNodeId/graphId) are this component's one
 * real integration point with the backend half of this stage. Neither is
 * backed by a codegen contract (like "knowledge"/"search" itself, this is
 * a request/reply intent, not a persistent state topic - see
 * KnowledgeSearchDialog.tsx's own identical posture), so there is nothing
 * in web_ui/src/lib/bridge-core/generated/ to check against; if the
 * backend half lands under different names, reconciling is a
 * find-and-replace of the two string literals below, not a redesign.
 */

interface GlobalSearchResult {
  chunkId: number;
  documentId: number;
  documentTitle: string;
  sourceUri: string;
  text: string;
  offsetStart: number;
  offsetEnd: number;
  /** Non-null only for a graph-sourced chunk (knowledge_store's
   * chunks.source_node_id, this stage's migration 5) - the node within
   * that graph this hit came from. */
  sourceNodeId: string | null;
  /** Non-null only for a graph-sourced chunk - the graph (chat_id) that
   * owns sourceNodeId, parsed server-side from documents.source_uri's own
   * synthetic "graph:{id}" scheme. */
  graphId: number | null;
}

function isHttpUrl(value: string): boolean {
  return /^https?:\/\//i.test(value);
}

function isGraphHit(
  result: GlobalSearchResult,
): result is GlobalSearchResult & { sourceNodeId: string; graphId: number } {
  return result.sourceNodeId != null && result.graphId != null;
}

function GlobalSearchResultCard({ transport, result }: { transport: WsTransport; result: GlobalSearchResult }) {
  const [expanded, setExpanded] = useState(false);
  const [jumping, setJumping] = useState(false);
  const [jumpError, setJumpError] = useState<string | null>(null);
  const { setCenter } = useReactFlow();
  const overlays = useOverlays();
  const graphHit = isGraphHit(result);

  function jumpToNode() {
    if (!graphHit || jumping) return;
    setJumping(true);
    setJumpError(null);
    transport
      .request("app-chat-library", "loadGraphAndFocusNode", [result.graphId, result.sourceNodeId])
      .then((value) => {
        const position = value as { x: number; y: number } | null;
        // A stale result: the graph was re-indexed since this node was
        // deleted from it. Not an error - the search hit was real at
        // index time, just no longer resolvable to a live location.
        if (!position) {
          setJumpError("This node no longer exists in that graph.");
          return;
        }
        setCenter(position.x, position.y, { zoom: 1, duration: motionDuration(300) });
        overlays.close();
      })
      .catch(() => setJumpError("Couldn't open that graph - see graphlink.log for details."))
      .finally(() => setJumping(false));
  }

  return (
    <li className="global-search-result-item">
      <button
        type="button"
        className="global-search-result-header"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="global-search-result-title">{result.documentTitle}</span>
        <span className="global-search-result-kind">{graphHit ? "Graph" : "Document"}</span>
        <span className="global-search-result-source">{result.sourceUri}</span>
      </button>
      {expanded && (
        <div className="global-search-result-body">
          <p className="global-search-result-excerpt">{result.text}</p>
          <div className="global-search-result-footer">
            <span className="global-search-result-offset">
              Offset {result.offsetStart}–{result.offsetEnd}
            </span>
            {graphHit ? (
              <button
                type="button"
                className="global-search-result-open-link"
                onClick={jumpToNode}
                disabled={jumping}
              >
                {jumping ? "Jumping…" : "Jump to node"}
              </button>
            ) : (
              isHttpUrl(result.sourceUri) && (
                <a
                  className="global-search-result-open-link"
                  href={result.sourceUri}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open source
                </a>
              )
            )}
          </div>
          {jumpError && (
            <p className="global-search-jump-error" role="alert">
              {jumpError}
            </p>
          )}
        </div>
      )}
    </li>
  );
}

export function GlobalSearchDialog({ transport }: { transport: WsTransport }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GlobalSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Same monotonic-requestId stale-response guard as KnowledgeSearchDialog's
  // own latestRequestId - see that component's comment for the full
  // rationale. Checked before ever touching state in a resolved/rejected
  // handler, so only the MOST RECENTLY SENT request's own response ever
  // wins, regardless of network arrival order.
  const latestRequestId = useRef(0);

  function runSearch() {
    const trimmed = query.trim();
    if (!trimmed || searching) return;
    const requestId = ++latestRequestId.current;
    setSearching(true);
    setError(null);
    transport
      .request("globalSearch", "search", [trimmed, 10])
      .then((value) => {
        if (requestId !== latestRequestId.current) return; // a newer search has since been sent
        const payload = value as { results: GlobalSearchResult[] };
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
    <Dialog name="global-search" title="Global Search" className="global-search-dialog">
      <div className="global-search-row">
        <input
          type="text"
          className="global-search-input"
          aria-label="Search every workspace"
          placeholder="Search every workspace's graphs and knowledge base…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") runSearch();
          }}
        />
        <button
          type="button"
          className="global-search-button"
          onClick={runSearch}
          disabled={searching || !query.trim()}
        >
          {searching ? "Searching…" : "Search"}
        </button>
      </div>

      {error && (
        <p className="global-search-empty" role="alert">
          {error}
        </p>
      )}

      {results != null && !error && (
        <>
          <h2 className="global-search-section-title">
            {results.length === 0
              ? "No results found"
              : `${results.length} result${results.length === 1 ? "" : "s"} found`}
          </h2>
          <ul className="global-search-result-list">
            {results.map((result) => (
              <GlobalSearchResultCard key={result.chunkId} transport={transport} result={result} />
            ))}
          </ul>
        </>
      )}
    </Dialog>
  );
}
