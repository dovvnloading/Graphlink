import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

/** Query is isolated from match metadata so highlighting does not rerender on navigation. */
const QueryContext = createContext({ query: "", setQuery: (_query: string) => {} });
const MatchesContext = createContext({
  matchIds: [] as readonly string[],
  setMatchIds: (_ids: readonly string[]) => {},
});

export function CanvasSearchProvider({ children }: { children: ReactNode }) {
  const [query, setQuery] = useState("");
  const [matchIds, setMatchIdsState] = useState<readonly string[]>([]);
  const setMatchIds = useMemo(() => (ids: readonly string[]) => {
    setMatchIdsState((before) =>
      before.length === ids.length && before.every((id, index) => id === ids[index]) ? before : ids);
  }, []);
  const queryValue = useMemo(() => ({ query, setQuery }), [query]);
  const matchesValue = useMemo(() => ({ matchIds, setMatchIds }), [matchIds, setMatchIds]);
  return <QueryContext.Provider value={queryValue}>
    <MatchesContext.Provider value={matchesValue}>{children}</MatchesContext.Provider>
  </QueryContext.Provider>;
}

export function useCanvasSearchQuery(): string {
  return useContext(QueryContext).query;
}
export function useSetCanvasSearchQuery(): (query: string) => void {
  return useContext(QueryContext).setQuery;
}
export function useCanvasSearchMatchIds(): readonly string[] {
  return useContext(MatchesContext).matchIds;
}
export function useSetCanvasSearchMatchIds(): (ids: readonly string[]) => void {
  return useContext(MatchesContext).setMatchIds;
}
