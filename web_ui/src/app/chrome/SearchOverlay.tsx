import { useReactFlow } from "@xyflow/react";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useCanvasSearchQuery, useSetCanvasSearchQuery } from "../canvas/CanvasSearchContext";
import type { SceneStore } from "../canvas/sceneStore";
import { motionDuration } from "../reducedMotion";
import { useOverlays } from "../overlays/overlays";

/**
 * The search overlay (Qt-removal plan R2.4) - search-overlay island's
 * successor. Searches both a node's title and its full text content, the
 * same haystack the legacy SearchOverlay matched conversation text against.
 *
 * ADR-012 stage 12.5: the query itself now lives in CanvasSearchContext
 * (this component still owns everything else - currentIndex, matches,
 * navigation) so NodeMarkdown.tsx can highlight matches inside every
 * rendered node card without prop-drilling through 15 *NodeView.tsx
 * components - see that Context's own doc for the full reasoning.
 */
export function SearchOverlay({ store }: { store: SceneStore }) {
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const overlays = useOverlays();
  const { setCenter } = useReactFlow();
  const query = useCanvasSearchQuery();
  const setQuery = useSetCanvasSearchQuery();
  const [currentIndex, setCurrentIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const isOpen = overlays.isOpen("search");

  // Unlike every other popover (Pins, View, Plugins, Reasoning, Model),
  // this shell was never registered as a surface element - it renders a
  // bare div rather than the shared <Popover>, so the outside-click
  // light-dismiss effect in overlays.tsx could never find it and clicking
  // away did nothing at all.
  useEffect(() => {
    overlays.registerSurfaceElement("search", shellRef.current);
    return () => overlays.registerSurfaceElement("search", null);
  });

  // Reset during render on the false->true transition - see
  // CommandPalette's identical fix for the full rationale.
  const [wasOpen, setWasOpen] = useState(isOpen);
  if (isOpen !== wasOpen) {
    setWasOpen(isOpen);
    if (isOpen) {
      setQuery("");
      setCurrentIndex(-1);
    }
  }

  useEffect(() => {
    if (isOpen) inputRef.current?.focus();
  }, [isOpen]);

  const matches = useMemo(() => {
    const term = query.toLowerCase().trim();
    if (!term) return [];
    return scene.nodes.filter(
      (n) => n.title.toLowerCase().includes(term) || n.content.toLowerCase().includes(term),
    );
  }, [query, scene.nodes]);

  function jumpTo(index: number) {
    const match = matches[index];
    if (match) setCenter(match.x, match.y, { zoom: 1, duration: motionDuration(300) });
  }

  function next() {
    if (matches.length === 0) return;
    const index = (currentIndex + 1) % matches.length;
    setCurrentIndex(index);
    jumpTo(index);
  }

  function previous() {
    if (matches.length === 0) return;
    const index = (currentIndex - 1 + matches.length) % matches.length;
    setCurrentIndex(index);
    jumpTo(index);
  }

  function onQueryChange(value: string) {
    setQuery(value);
    setCurrentIndex(-1);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" && event.shiftKey) {
      event.preventDefault();
      previous();
    } else if (event.key === "Enter") {
      event.preventDefault();
      next();
    }
    // Escape is handled globally by the overlay system.
  }

  if (!isOpen) return null;

  const current = currentIndex + 1;
  const tone = matches.length === 0 && query ? "error" : current > 0 ? "active" : "idle";

  return (
    <div className="search-overlay-shell" ref={shellRef}>
      <input
        ref={inputRef}
        className="search-overlay-input"
        type="text"
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Find node…"
        aria-label="Search the canvas"
        autoComplete="off"
        spellCheck={false}
      />
      <span className="search-overlay-count" data-tone={tone}>
        {current} / {matches.length}
      </span>
      <button
        type="button"
        className="search-overlay-icon-btn"
        onClick={previous}
        aria-label="Previous match (Shift+Enter)"
      >
        ▲
      </button>
      <button
        type="button"
        className="search-overlay-icon-btn"
        onClick={next}
        aria-label="Next match (Enter)"
      >
        ▼
      </button>
      <button
        type="button"
        className="search-overlay-icon-btn"
        onClick={overlays.close}
        aria-label="Close (Esc)"
      >
        ×
      </button>
    </div>
  );
}
