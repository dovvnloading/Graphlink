import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

/** Live query and matching node IDs for canvas search and subsequent dimming. */
interface CanvasSearchContextValue {
  query: string;
  setQuery: (query: string) => void;
  matchIds: readonly string[];
  setMatchIds: (ids: readonly string[]) => void;
}
const CanvasSearchContext = createContext<CanvasSearchContextValue>({
  query: "",
  setQuery: () => {},
  matchIds: [],
  setMatchIds: () => {},
});

export function CanvasSearchProvider({ children }: { children: ReactNode }) {
  const [query, setQuery] = useState("");
  const [matchIds, setMatchIds] = useState<readonly string[]>([]);
  const value = useMemo(() => ({ query, setQuery, matchIds, setMatchIds }), [query, matchIds]);
  return <CanvasSearchContext.Provider value={value}>{children}</CanvasSearchContext.Provider>;
}

export function useCanvasSearchQuery(): string {
  return useContext(CanvasSearchContext).query;
}

export function useSetCanvasSearchQuery(): (query: string) => void {
  return useContext(CanvasSearchContext).setQuery;
}

export function useCanvasSearchMatchIds(): readonly string[] {
  return useContext(CanvasSearchContext).matchIds;
}

export function useSetCanvasSearchMatchIds(): (ids: readonly string[]) => void {
  return useContext(CanvasSearchContext).setMatchIds;
}
