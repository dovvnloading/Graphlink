import { useReactFlow } from "@xyflow/react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import { Dialog, useOverlays } from "../overlays/overlays";
import { Icon } from "../ui/icons/Icon";
import { Kbd } from "../ui/Kbd";
import { buildCommands, type CommandCategory, type PaletteCommand } from "./commands";
import "./CommandPalette.css";

const CATEGORIES: CommandCategory[] = ["Canvas", "Graph", "Navigate", "App"];
const RECENTS_KEY = "graphlink:command-palette:recent";
function readRecentIds(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(RECENTS_KEY) || "[]");
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string").slice(0, 8) : [];
  } catch { return []; }
}
function saveRecentIds(ids: string[]) {
  try { localStorage.setItem(RECENTS_KEY, JSON.stringify(ids)); }
  catch { /* Palette remains fully usable without storage. */ }
}

/** Match fuzzy subsequences while preserving the real character offsets for highlighting. */
function positionsIn(value: string, term: string): number[] | null {
  const needle = term.toLowerCase().replace(/\s+/g, "");
  if (!needle) return [];
  const indices: number[] = [];
  let index = 0;
  for (let i = 0; i < value.length && index < needle.length; i++) {
    if (value[i].toLowerCase() === needle[index]) {
      indices.push(i);
      index++;
    }
  }
  return index === needle.length ? indices : null;
}

function resultScore(command: PaletteCommand, term: string): { score: number; alias?: string } | null {
  const q = term.toLowerCase().trim();
  if (!q) return { score: 0 };
  const start = command.name.toLowerCase().indexOf(q);
  if (start >= 0) return { score: 200 - start };
  const namePositions = positionsIn(command.name, q);
  if (namePositions) {
    const span = namePositions[namePositions.length - 1] - namePositions[0];
    return { score: 120 - span };
  }
  for (const alias of command.aliases) {
    const direct = alias.toLowerCase().indexOf(q);
    if (direct >= 0) return { score: 90 - direct, alias };
    const positions = positionsIn(alias, q);
    if (positions) return { score: 50 - positions[positions.length - 1] + positions[0], alias };
  }
  return null;
}

function Highlight({ value, query }: { value: string; query: string }) {
  const positions = positionsIn(value, query);
  if (!positions?.length) return <>{value}</>;
  const highlighted = new Set(positions);
  const groups: { text: string; selected: boolean }[] = [];
  for (let index = 0; index < value.length; index++) {
    const selected = highlighted.has(index);
    const last = groups[groups.length - 1];
    if (last?.selected === selected) last.text += value[index];
    else groups.push({ text: value[index], selected });
  }
  return <>{groups.map((group, index) => group.selected
    ? <mark key={index} className="palette-match">{group.text}</mark>
    : <span key={index}>{group.text}</span>)}</>;
}

type Result = { command: PaletteCommand; category: CommandCategory | "Recent"; score: number; alias?: string };

/** Search, recents, icons and shortcuts share a single keyboard-operated listbox. */
export function CommandPalette({ store }: { store: SceneStore }) {
  const overlays = useOverlays();
  const rf = useReactFlow();
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [recentIds, setRecentIds] = useState<string[]>(readRecentIds);
  const resultsRef = useRef<HTMLUListElement>(null);
  const isOpen = overlays.isOpen("palette");
  const [wasOpen, setWasOpen] = useState(isOpen);
  if (isOpen !== wasOpen) {
    setWasOpen(isOpen);
    if (isOpen) {
      setQuery("");
      setSelectedIndex(0);
    }
  }

  const commands = useMemo(() => buildCommands(store, rf, overlays), [store, rf, overlays]);
  const results = useMemo((): Result[] => {
    const term = query.trim();
    const matches = commands.filter((command) => command.enabled()).flatMap((command) => {
      const match = resultScore(command, term);
      return match ? [{ command, category: command.category, ...match }] : [];
    });
    if (term) return matches.sort((a, b) => b.score - a.score || a.command.name.localeCompare(b.command.name));
    return matches.map((result) => ({
      ...result, category: recentIds.includes(result.command.id) ? "Recent" as const : result.command.category,
    })).sort((a, b) => {
      if (a.category === "Recent" || b.category === "Recent")
        return a.category === b.category
          ? recentIds.indexOf(a.command.id) - recentIds.indexOf(b.command.id)
          : a.category === "Recent" ? -1 : 1;
      return CATEGORIES.indexOf(a.category) - CATEGORIES.indexOf(b.category)
        || a.command.name.localeCompare(b.command.name);
    });
  }, [commands, query, recentIds]);

  const activeIndex = Math.min(selectedIndex, Math.max(results.length - 1, 0));
  useEffect(() => {
    if (isOpen) resultsRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView?.({ block: "nearest" });
  }, [activeIndex, isOpen, results]);

  function execute(id: string) {
    const command = commands.find((item) => item.id === id);
    if (!command) return;
    const next = [id, ...recentIds.filter((item) => item !== id)].slice(0, 8);
    setRecentIds(next);
    saveRecentIds(next);
    // The overlay manager is single-open. Closing first keeps new dialogs open.
    overlays.close();
    command.run();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setSelectedIndex((index) => Math.max(0,
        Math.min(results.length - 1, index + (event.key === "ArrowDown" ? 1 : -1))));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const result = results[activeIndex];
      if (result) execute(result.command.id);
    }
  }

  if (!isOpen) return null;
  return (
    <Dialog name="palette" title="Command palette" size="md" className="palette-dialog">
      <input type="search" className="palette-search-input" autoComplete="off" spellCheck={false}
        placeholder="Type a command…" aria-label="Search commands"
        aria-controls="palette-results" aria-activedescendant={results[activeIndex] ? `palette-option-${results[activeIndex].command.id}` : undefined}
        value={query} onChange={(event) => { setQuery(event.target.value); setSelectedIndex(0); }}
        onKeyDown={onKeyDown} />
      <ul id="palette-results" className="palette-results" role="listbox" aria-label="Commands" ref={resultsRef}>
        {results.length === 0 && <li role="presentation" className="palette-empty">No matching commands</li>}
        {results.map((result, index) => <ReactCommandRow key={result.command.id}
          item={result} groupStart={index === 0 || results[index - 1].category !== result.category}
          selected={index === activeIndex} onHover={() => setSelectedIndex(index)}
          onExecute={() => execute(result.command.id)} query={query} />)}
      </ul>
      <p className="palette-hint"><Kbd shortcut="↑" /> <Kbd shortcut="↓" /> to navigate · <Kbd shortcut="Enter" /> to run</p>
    </Dialog>
  );
}

function ReactCommandRow({ item, groupStart, selected, onHover, onExecute, query }: {
  item: Result; groupStart: boolean; selected: boolean;
  onHover: () => void; onExecute: () => void; query: string;
}) {
  return <>
    {groupStart && <li role="presentation" className="palette-category">{item.category}</li>}
    {/* The input retains focus and owns Arrow/Enter; hover and click mirror it. */}
    {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events */}
    <li id={`palette-option-${item.command.id}`} role="option" aria-selected={selected}
      className={"palette-result" + (selected ? " selected" : "")}
      onMouseEnter={onHover} onClick={onExecute}>
      <Icon name={item.command.icon} size="md" />
      <span className="palette-result-label">
        <Highlight value={item.command.name} query={query} />
        {item.alias && <span className="palette-result-alias"><Highlight value={item.alias} query={query} /></span>}
      </span>
      {item.command.shortcut && <Kbd shortcut={item.command.shortcut} />}
    </li>
  </>;
}
