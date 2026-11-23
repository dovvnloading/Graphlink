import { useEffect, useMemo, useState } from "react";
import helpDataUrl from "./help-data/sections.json?url";
import type { HelpItem, HelpSection } from "./help-data/sections";
import { Dialog } from "../overlays/overlays";
import { Icon } from "../ui/icons/Icon";
import type { IconName } from "../ui/icons/registry";
import { Kbd } from "../ui/Kbd";
import { resolveShortcut, type ShortcutId } from "./shortcuts";
import "./HelpDialog.css";

/**
 * The Help panel (Qt-removal plan R2.5) - help-web's SPA successor.
 *
 * Static content, no backend state: Python never learned which section was
 * open even in the legacy island. No WS topic, no client store.
 *
 * SEARCH IS THE MAIN AFFORDANCE, and it was missing. The panel holds ~70
 * items across ten sections, and the only way in was to guess which section
 * a thing lived under - which is precisely the guess a person opening Help
 * has already failed to make. Typing now filters every item in every
 * section at once, and a query REPLACES the section navigation rather than
 * narrowing within it: search and browse are two ways to reach the same
 * content, and letting them run at once (a filtered list inside a chosen
 * section) means an item you can see in the count but not on screen,
 * because the wrong section happens to be selected.
 *
 * KEY CHORDS ARE DATA, not prose. Shortcut items used to spell their keys
 * into the title text ("Ctrl + T / Ctrl + L / Ctrl + S" as one heading for
 * three unrelated commands), which read as one item, could not be searched
 * for by the command's name, and gave the keys no visual weight at all.
 * They are a `keys` field now, rendered as <kbd>, so one item means one
 * command and the chord is the thing your eye lands on.
 *
 * The rail's intro paragraph is gone. It said "Graphlink is a visual AI
 * workspace. Start with the overview, then jump directly to the workflow,
 * tool, or project area you need" - a sentence of positioning copy and a
 * sentence instructing the reader to use the list of section buttons
 * directly beneath it, which the list already affords.
 */


const HELP_ICONS: Record<string, IconName> = {
  "Start Here": "help", "The Canvas": "workspace", "Node Types": "layers",
  "Finding Things": "search", "Organizing": "organize", Plugins: "puzzle",
  "The Builder": "builder", "The Agent": "agent", "Models and Settings": "settings",
  "Saving and Output": "download", Keyboard: "keyboard",
};

const SHORTCUT_INFO: Record<ShortcutId, { group: string; action: string; description: string }> = {
  "new-chat": { group: "Project", action: "New chat", description: "Start a fresh graph." },
  "toggle-library": { group: "Project", action: "Open library", description: "Find saved graphs." },
  "save-chat": { group: "Project", action: "Save chat", description: "Save the current graph." },
  "toggle-palette": { group: "Finding", action: "Command palette", description: "Find a command by name." },
  "toggle-search": { group: "Finding", action: "Search this graph", description: "Find text in canvas nodes." },
  "toggle-quick-switcher": { group: "Finding", action: "Quick switcher", description: "Switch to a saved graph." },
  "create-frame": { group: "Editing", action: "Create frame", description: "Frame selected nodes." },
  "create-container": { group: "Editing", action: "Create container", description: "Group selected nodes." },
  "undo": { group: "Editing", action: "Undo", description: "Undo a canvas change." },
  "redo": { group: "Editing", action: "Redo", description: "Redo an undone change." },
  "compare-branches": { group: "Branches", action: "Compare branches", description: "Compare selected branches." },
  "synthesize-branches": { group: "Branches", action: "Synthesize branches", description: "Combine selected branches." },
  "navigate-up": { group: "Branches", action: "Navigate to parent", description: "Move through the graph." },
  "navigate-down": { group: "Branches", action: "Navigate to child", description: "Move through the graph." },
  "navigate-left": { group: "Branches", action: "Navigate left", description: "Move through siblings." },
  "navigate-right": { group: "Branches", action: "Navigate right", description: "Move through siblings." },
};

/** Discover actual chords from the dispatch resolver rather than copying shortcuts by hand. */
function generatedKeyboardSection(source: HelpSection): HelpSection {
  const chords = new Map<ShortcutId, string[]>();
  const keys = [..."abcdefghijklmnopqrstuvwxyz", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"];
  for (const shiftKey of [false, true]) {
    for (const key of keys) {
      const id = resolveShortcut({ key, ctrlKey: true, metaKey: false, shiftKey, altKey: false });
      if (!id) continue;
      const displayKey = key.startsWith("Arrow")
        ? { ArrowUp: "↑", ArrowDown: "↓", ArrowLeft: "←", ArrowRight: "→" }[key as "ArrowUp" | "ArrowDown" | "ArrowLeft" | "ArrowRight"]
        : key.toUpperCase();
      const chord = "Ctrl+" + (shiftKey ? "Shift+" : "") + displayKey;
      const existing = chords.get(id) ?? [];
      if (!existing.includes(chord)) chords.set(id, [...existing, chord]);
    }
  }
  const subsections = ["Project", "Finding", "Editing", "Branches"].map((title) => ({
    title,
    items: (Object.entries(SHORTCUT_INFO) as [ShortcutId, typeof SHORTCUT_INFO[ShortcutId]][])
      .filter(([id, details]) => details.group === title && chords.has(id))
      .map(([id, details]) => ({ action: details.action, description: details.description, keys: chords.get(id)! })),
  }));
  return { ...source, subsections: [...subsections, ...source.subsections.filter((item) => item.title === "In a Text Field")] };
}

interface Match {
  section: HelpSection;
  subsectionTitle: string;
  item: HelpItem;
}

/** Substring match over everything a person might type: the item, its
 *  subsection, its section, and the chords themselves - so "ctrl+f" finds
 *  canvas search by its key and not only by its name. */
function matches(query: string, sections: HelpSection[]): Match[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const found: Match[] = [];
  for (const section of sections) {
    for (const subsection of section.subsections) {
      for (const item of subsection.items) {
        const haystack = [
          item.action,
          item.description,
          subsection.title,
          section.name,
          ...(item.keys ?? []),
        ]
          .join(" ")
          .toLowerCase();
        if (haystack.includes(q)) found.push({ section, subsectionTitle: subsection.title, item });
      }
    }
  }
  return found;
}

function Keys({ keys }: { keys: string[] }) {
  return (
    <span className="help-item-keys">
      {keys.map((chord, index) => (
        <span key={chord}>
          {index > 0 && <span className="help-item-keys-or">or</span>}
          <Kbd className="help-kbd" shortcut={chord} />
        </span>
      ))}
    </span>
  );
}

function Item({ item, context }: { item: HelpItem; context?: string }) {
  return (
    <div className="help-item">
      <p className="help-item-action">
        <span className="help-item-name">{item.action}</span>
        {item.keys && <Keys keys={item.keys} />}
      </p>
      <p className="help-item-description">{item.description}</p>
      {context && <p className="help-item-context">{context}</p>}
    </div>
  );
}

export function HelpDialog() {
  const [sections, setSections] = useState<HelpSection[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [activeSectionName, setActiveSectionName] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    let disposed = false;
    // This effect only mounts after LazySurface opens Help. Fetching an
    // asset URL (rather than importing JSON) keeps reference prose out of
    // every JS chunk, including the lazily loaded HelpDialog chunk.
    void fetch(helpDataUrl).then(async (response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const content: unknown = await response.json();
      if (!Array.isArray(content) || content.length === 0) throw new Error("Empty help content");
      return content as HelpSection[];
    }).then(
      (content) => { if (!disposed) setSections(content); },
      () => { if (!disposed) setLoadError("Help content could not load. Close and reopen Help to try again."); },
    );
    return () => { disposed = true; };
  }, []);

  const availableSections = useMemo(() => (sections ?? []).map((section) =>
    section.name === "Keyboard" ? generatedKeyboardSection(section) : section), [sections]);
  const results = useMemo(() => matches(query, availableSections), [query, availableSections]);
  const searching = query.trim().length > 0;
  const activeSection = availableSections.find((section) => section.name === activeSectionName) ?? availableSections[0];

  return (
    <Dialog name="help" title="Help" size="xl" className="help-dialog">
      {loadError ? <p role="alert">{loadError}</p> : !sections ? <p role="status">Loading help…</p> : (
      <div className="help-shell">
        <nav className="help-rail" aria-label="Help sections">
          <input
            type="search"
            className="help-search"
            placeholder="Search help"
            value={query}
            aria-label="Search help"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              // Escape clears the query first and closes the dialog only
              // when there is nothing left to clear. preventDefault is the
              // overlay system's own "I handled this" signal (overlays.tsx),
              // so the second Escape still closes normally.
              if (event.key === "Escape" && query) {
                event.preventDefault();
                setQuery("");
              }
            }}
          />
          <div className="help-rail-buttons">
            {availableSections.map((section) => (
              <button
                key={section.name}
                type="button"
                className={
                  "help-rail-button" +
                  (!searching && section.name === activeSection.name ? " active" : "")
                }
                aria-current={
                  !searching && section.name === activeSection.name ? "page" : undefined
                }
                onClick={() => {
                  setQuery("");
                  setActiveSectionName(section.name);
                }}
              >
                <Icon name={HELP_ICONS[section.name] ?? "help"} size="sm" />
                <span>{section.name}</span>
              </button>
            ))}
          </div>
        </nav>

        {searching ? (
          <div className="help-content" role="region" aria-label="Help search results">
            <h1 className="help-content-title">Results</h1>
            <p className="help-content-description" role="status">
              {results.length === 0
                ? `Nothing matches "${query.trim()}".`
                : `${results.length} ${results.length === 1 ? "entry" : "entries"} for "${query.trim()}".`}
            </p>
            <div className="help-scroll-area">
              {results.map((match, index) => (
                <Item
                  key={`${match.section.name}-${match.subsectionTitle}-${index}`}
                  item={match.item}
                  context={`${match.section.name} · ${match.subsectionTitle}`}
                />
              ))}
            </div>
          </div>
        ) : (
          <div className="help-content" role="region" aria-label={activeSection.name}>
            <h1 className="help-content-title">{activeSection.name}</h1>
            <p className="help-content-description">{activeSection.description}</p>

            <div className="help-scroll-area">
              {activeSection.subsections.map((subsection) => (
                <section className="help-section-block" key={subsection.title}>
                  <h2 className="help-section-title">{subsection.title}</h2>
                  {subsection.items.map((item, index) => (
                    <Item key={`${subsection.title}-${index}`} item={item} />
                  ))}
                </section>
              ))}
            </div>
          </div>
        )}
      </div>
      )}
    </Dialog>
  );
}
