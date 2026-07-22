import { useReactFlow } from "@xyflow/react";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import { useOverlays } from "../overlays/overlays";

/**
 * The search overlay (Qt-removal plan R2.4) - search-overlay island's
 * successor. Searches live node titles (real, today); once R3 nodes carry
 * real text content, matching extends to it the same way the legacy
 * SearchOverlay matched conversation text - no interface change needed,
 * just a richer haystack per node.
 */
export function SearchOverlay({ store }: { store: SceneStore }) {
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const overlays = useOverlays();
  const { setCenter } = useReactFlow();
  const [query, setQuery] = useState("");
  const [currentIndex, setCurrentIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const isOpen = overlays.isOpen("search");

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
    return scene.nodes.filter((n) => n.title.toLowerCase().includes(term));
  }, [query, scene.nodes]);

  function jumpTo(index: number) {
    const match = matches[index];
    if (match) setCenter(match.x, match.y, { zoom: 1, duration: 300 });
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
    <div className="search-overlay-shell">
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
