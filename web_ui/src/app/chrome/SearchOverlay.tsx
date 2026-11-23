import { useReactFlow } from "@xyflow/react";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  useCanvasSearchQuery, useSetCanvasSearchQuery, useSetCanvasSearchMatchIds,
} from "../canvas/CanvasSearchContext";
import type { SceneStore } from "../canvas/sceneStore";
import { motionDuration } from "../reducedMotion";
import { useOverlays } from "../overlays/overlays";
import { TextField } from "../ui/TextField";
import { IconButton } from "../ui/IconButton";
import "./SearchOverlay.css";

export function SearchOverlay({ store }: { store: SceneStore }) {
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const overlays = useOverlays();
  const flow = useReactFlow();
  const query = useCanvasSearchQuery();
  const setQuery = useSetCanvasSearchQuery();
  const setMatchIds = useSetCanvasSearchMatchIds();
  const [currentIndex, setCurrentIndex] = useState(-1);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const lastQueried = useRef("");
  const isOpen = overlays.isOpen("search");

  useEffect(() => {
    overlays.registerSurfaceElement("search", shellRef.current);
    return () => overlays.registerSurfaceElement("search", null);
  }, [overlays]);

  const matches = useMemo(() => {
    const term = query.toLocaleLowerCase().trim();
    if (!isOpen || !term) return [];
    return scene.nodes.filter((node) =>
      node.title.toLocaleLowerCase().includes(term) || node.content.toLocaleLowerCase().includes(term));
  }, [query, isOpen, scene.nodes]);

  function jumpTo(index: number) {
    const match = matches[index];
    if (!match) return;
    // Selection is kept in React Flow's transient state, not persisted to the scene.
    flow.setNodes?.((nodes) => nodes.map((node) =>
      Boolean(node.selected) === (node.id === match.id) ? node : { ...node, selected: node.id === match.id }));
    const internal = flow.getInternalNode?.(match.id);
    if (internal && flow.getNodesBounds) {
      const bounds = flow.getNodesBounds([match.id]);
      flow.setCenter(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2,
        { zoom: flow.getZoom?.() ?? 1, duration: motionDuration(300) });
    } else {
      flow.setCenter(match.x, match.y, { zoom: 1, duration: motionDuration(300) });
    }
  }

  function move(direction: number) {
    if (matches.length === 0) return;
    const index = (currentIndex + direction + matches.length) % matches.length;
    setCurrentIndex(index);
    jumpTo(index);
  }

  function onQueryChange(value: string) {
    setQuery(value);
    setCurrentIndex(value.trim() ? 0 : -1);
  }

  // Pan on a changed query, not on unrelated scene/selection publishes.
  useEffect(() => {
    if (!isOpen) return;
    setMatchIds(matches.map((node) => node.id));
    const normalized = query.trim();
    if (normalized === lastQueried.current) return;
    lastQueried.current = normalized;
    if (matches.length === 0) return;
    const match = matches[0];
    flow.setNodes?.((nodes) => nodes.map((node) =>
      Boolean(node.selected) === (node.id === match.id) ? node : { ...node, selected: node.id === match.id }));
    flow.setCenter(match.x, match.y, { zoom: 1, duration: motionDuration(300) });
  }, [isOpen, query, matches, setMatchIds, flow]);

  useEffect(() => {
    if (isOpen) {
      previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      shellRef.current?.querySelector<HTMLInputElement>("input")?.focus();
      return;
    }
    setQuery("");
    lastQueried.current = "";
    setMatchIds([]);
    previousFocus.current?.focus();
    previousFocus.current = null;
  }, [isOpen, setQuery, setMatchIds]);

  if (!isOpen) return null;
  const current = matches.length === 0 ? 0 : Math.min(Math.max(0, currentIndex), matches.length - 1) + 1;
  return (
    <div className="search-overlay-shell" ref={shellRef} role="search" aria-label="Canvas find toolbar">
      <TextField label="Search the canvas" size="sm" type="search" className="search-overlay-field"
        placeholder="Find node…" value={query} onValueChange={onQueryChange}
        autoComplete="off" spellCheck={false} onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            move(event.shiftKey ? -1 : 1);
          }
        }} />
      <span className="search-overlay-count" role="status" aria-live="polite"
        data-tone={matches.length === 0 && query ? "error" : "idle"}>
        {current} of {matches.length}
      </span>
      <IconButton icon="arrow-up" label="Previous match" shortcut="Shift+Enter" size="sm"
        disabled={matches.length === 0} onClick={() => move(-1)} />
      <IconButton icon="arrow-down" label="Next match" shortcut="Enter" size="sm"
        disabled={matches.length === 0} onClick={() => move(1)} />
      <IconButton icon="close" label="Close search" shortcut="Escape" size="sm" onClick={overlays.close} />
    </div>
  );
}
