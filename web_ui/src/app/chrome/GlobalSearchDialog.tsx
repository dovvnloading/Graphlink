import { useReactFlow } from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { motionDuration } from "../reducedMotion";
import { Dialog, useOverlays } from "../overlays/overlays";
import { knowledgeSourceLabel } from "../knowledgeSourceLabel";
import { Button } from "../ui/Button";
import { Icon } from "../ui/icons/Icon";
import { InlineAlert } from "../ui/InlineAlert";
import { SegmentedControl } from "../ui/SegmentedControl";
import "./GlobalSearchDialog.css";

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

function GlobalSearchResultCard({ transport, result, selected }: {
  transport: WsTransport; result: GlobalSearchResult; selected: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [jumping, setJumping] = useState(false);
  const [jumpError, setJumpError] = useState<string | null>(null);
  const { setCenter } = useReactFlow();
  const overlays = useOverlays();
  const graphHit = isGraphHit(result);
  const sourceLabel = knowledgeSourceLabel(result.sourceUri, result.documentTitle);

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
    <li className="global-search-result-item" role="option" aria-selected={selected}
      id={`global-search-result-${result.chunkId}`}>
      <Button
        variant="ghost"
        iconStart={graphHit ? "workspace" : "knowledge"}
        className="global-search-result-header"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="global-search-result-title">{result.documentTitle}</span>
        <span className="global-search-result-kind">{graphHit ? "Graph" : "Document"}</span>
        {sourceLabel !== result.documentTitle && (
          <span className="global-search-result-source">{sourceLabel}</span>
        )}
      </Button>
      {expanded && (
        <div className="global-search-result-body">
          <p className="global-search-result-excerpt">{result.text}</p>
          <div className="global-search-result-footer">
            <span className="global-search-result-offset">
              Offset {result.offsetStart}–{result.offsetEnd}
            </span>
            {graphHit ? (
              <Button variant="ghost" size="sm" iconStart="arrow-right"
                className="global-search-result-open-link"
                onClick={jumpToNode} loading={jumping}
              >
                {jumping ? "Jumping…" : "Jump to node"}
              </Button>
            ) : (
              isHttpUrl(result.sourceUri) && (
                <a
                  className="global-search-result-open-link"
                  href={result.sourceUri}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open source <Icon name="external-link" size="xs" />
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


type SearchScope = "all" | "graphs" | "knowledge";
const RECENT_LIMIT = 6;
const SEARCH_DEBOUNCE_MS = 250;

/** localStorage is isolated per browser profile; a viewer URL scope distinguishes shared profiles. */
function recentStorageKey(): string {
  const viewer = new URLSearchParams(window.location.search).get("viewer") || "local";
  return `graphlink:global-search:recent:${window.location.pathname}:${viewer}`;
}
function loadRecents(): string[] {
  try {
    const stored: unknown = JSON.parse(window.localStorage.getItem(recentStorageKey()) || "[]");
    return Array.isArray(stored)
      ? stored.filter((item): item is string => typeof item === "string" && !!item.trim()).slice(0, RECENT_LIMIT)
      : [];
  } catch { return []; }
}
function saveRecents(next: string[]): void {
  try { window.localStorage.setItem(recentStorageKey(), JSON.stringify(next)); }
  catch { /* Storage can be disabled or full. Search still works. */ }
}

export function GlobalSearchDialog({ transport }: { transport: WsTransport }) {
  const overlays = useOverlays();
  const { setCenter } = useReactFlow();
  const isOpen = overlays.isOpen("global-search");
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<SearchScope>("all");
  const [results, setResults] = useState<GlobalSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recents, setRecents] = useState<string[]>(loadRecents);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const latestRequestId = useRef(0);
  const lastRequested = useRef("");

  const runSearch = useCallback((searchText: string) => {
    const trimmed = searchText.trim();
    if (!trimmed || lastRequested.current === trimmed) return;
    lastRequested.current = trimmed;
    const requestId = ++latestRequestId.current;
    setSearching(true);
    setError(null);
    transport.request("globalSearch", "search", [trimmed, 10])
      .then((value) => {
        if (requestId !== latestRequestId.current) return;
        const payload = value as { results: GlobalSearchResult[] };
        setResults(Array.isArray(payload.results) ? payload.results : []);
        setSelectedIndex(0);
        setRecents((previous) => {
          const next = [trimmed, ...previous.filter((item) => item !== trimmed)].slice(0, RECENT_LIMIT);
          saveRecents(next);
          return next;
        });
      })
      .catch(() => {
        if (requestId !== latestRequestId.current) return;
        setError("Search failed. Check the connection and try again.");
      })
      .finally(() => {
        if (requestId === latestRequestId.current) setSearching(false);
      });
  }, [transport]);

  useEffect(() => {
    if (!isOpen || !query.trim()) return;
    const timer = window.setTimeout(() => runSearch(query), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [isOpen, query, runSearch]);

  const visible = useMemo(() => (results ?? []).filter((result) =>
    scope === "all" || (scope === "graphs" ? isGraphHit(result) : !isGraphHit(result)),
  ), [results, scope]);
  const graphs = visible.filter(isGraphHit);
  const knowledge = visible.filter((result) => !isGraphHit(result));
  const activeIndex = Math.min(selectedIndex, Math.max(visible.length - 1, 0));
  const selected = visible[activeIndex];

  function changeQuery(value: string) {
    if (value === query) return;
    ++latestRequestId.current; // Invalidates a reply sent for earlier text, immediately.
    lastRequested.current = "";
    setQuery(value);
    setResults(null);
    setError(null);
    setSearching(false);
    setSelectedIndex(0);
  }

  async function activateResult(result: GlobalSearchResult) {
    if (!isGraphHit(result)) {
      if (isHttpUrl(result.sourceUri)) window.open(result.sourceUri, "_blank", "noopener,noreferrer");
      return;
    }
    try {
      const position = await transport.request("app-chat-library", "loadGraphAndFocusNode",
        [result.graphId, result.sourceNodeId]) as { x: number; y: number } | null;
      if (!position) {
        setError("This node no longer exists in that graph.");
        return;
      }
      setCenter(position.x, position.y, { zoom: 1, duration: motionDuration(300) });
      overlays.close();
    } catch {
      setError("Couldn't open that graph. Check the connection and try again.");
    }
  }

  return (
    <Dialog name="global-search" {...{ title: "Global search" }} size="md" className="global-search-dialog">
      <div className="global-search-row">
        <input type="search" className="global-search-input"
          aria-label="Search every workspace"
          aria-controls="global-search-results"
          aria-activedescendant={selected ? `global-search-result-${selected.chunkId}` : undefined}
          placeholder="Search every workspace's graphs and knowledge base…"
          value={query} onChange={(event) => changeQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              if (visible.length) setSelectedIndex((index) =>
                (index + (event.key === "ArrowDown" ? 1 : -1) + visible.length) % visible.length);
            } else if (event.key === "Enter") {
              event.preventDefault();
              if (results && selected) void activateResult(selected);
              else runSearch(query);
            }
          }} />
        <Button variant="primary" iconStart="search" onClick={() => runSearch(query)}
          disabled={!query.trim() || searching} loading={searching}>Search</Button>
      </div>
      <SegmentedControl label="Search scope" size="sm" value={scope}
        options={[
          { value: "all", label: "All" },
          { value: "graphs", label: "Graphs", icon: "workspace" },
          { value: "knowledge", label: "Knowledge", icon: "knowledge" },
        ]}
        onChange={(next) => { setScope(next as SearchScope); setSelectedIndex(0); }} />
      {!query.trim() && recents.length > 0 && <section className="global-search-recent">
        <h3>Recent searches</h3>
        <div className="global-search-recent-items">{recents.map((item) => (
          <Button key={item} size="sm" variant="ghost" iconStart="history" onClick={() => {
            changeQuery(item);
            runSearch(item);
          }}>{item}</Button>
        ))}</div>
      </section>}
      {error && <InlineAlert variant="error" {...{ title: "Search unavailable" }} body={error} />}
      {results && !error && <div id="global-search-results" role="region" aria-label="Search results">
        <p className="global-search-section-title" role="status">
          {visible.length === 0 ? "No results found" : `${visible.length} result${visible.length === 1 ? "" : "s"} found`}
        </p>
        {([
          { label: "Graphs", items: graphs },
          { label: "Knowledge", items: knowledge },
        ] as const).map((group) => group.items.length > 0 && <section key={group.label}>
          <h3 className="global-search-group-heading">
            <Icon name={group.label === "Graphs" ? "workspace" : "knowledge"} size="sm" />
            {group.label}
          </h3>
          <ul className="global-search-result-list" role="listbox" aria-label={group.label + " results"}>
            {group.items.map((result) => (
              <GlobalSearchResultCard key={result.chunkId} transport={transport} result={result}
                selected={selected?.chunkId === result.chunkId} />
            ))}
          </ul>
        </section>)}
      </div>}
    </Dialog>
  );
}
