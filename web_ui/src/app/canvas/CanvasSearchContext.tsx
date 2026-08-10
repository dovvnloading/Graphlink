import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

/**
 * ADR-012 stage 12.5: the live canvas search query (SearchOverlay.tsx's own
 * input value), threaded via Context so NodeMarkdown.tsx can highlight
 * matches inside every rendered node card - see NodeMarkdown.tsx's own doc
 * for why it reuses documentViewSearchHighlight.ts's plugin rather than a
 * bespoke one. Context, not a prop threaded through all 15 `*NodeView.tsx`
 * components' own `data` shape (ExecutionLimitsContext.tsx's own doc gives
 * the identical reasoning): this is ephemeral, client-only UI state, not
 * something that belongs on the wire-synced SceneNodeRow model, and prop-
 * drilling it through every node kind for one leaf renderer would duplicate
 * the same plumbing 15 times over.
 *
 * The query itself - not matches/currentIndex/navigation - is the only
 * piece that crosses this boundary. SearchOverlay.tsx still owns which NODE
 * is "current" (it centers the viewport on it), a concept the per-node
 * highlighter here has no need for: every visible node highlights every
 * occurrence of the live query independently, regardless of which node
 * SearchOverlay's own Next/Previous last jumped to.
 */

interface CanvasSearchContextValue {
  query: string;
  setQuery: (query: string) => void;
}

const CanvasSearchContext = createContext<CanvasSearchContextValue>({
  query: "",
  setQuery: () => {},
});

export function CanvasSearchProvider({ children }: { children: ReactNode }) {
  const [query, setQuery] = useState("");
  const value = useMemo(() => ({ query, setQuery }), [query]);
  return <CanvasSearchContext.Provider value={value}>{children}</CanvasSearchContext.Provider>;
}

export function useCanvasSearchQuery(): string {
  return useContext(CanvasSearchContext).query;
}

export function useSetCanvasSearchQuery(): (query: string) => void {
  return useContext(CanvasSearchContext).setQuery;
}
