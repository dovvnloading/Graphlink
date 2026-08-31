import { useMemo, useState } from "react";
import { HELP_SECTIONS, type HelpItem, type HelpSection } from "./help-data/sections";
import { Dialog } from "../overlays/overlays";

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

interface Match {
  section: HelpSection;
  subsectionTitle: string;
  item: HelpItem;
}

/** Substring match over everything a person might type: the item, its
 *  subsection, its section, and the chords themselves - so "ctrl+f" finds
 *  canvas search by its key and not only by its name. */
function matches(query: string): Match[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const found: Match[] = [];
  for (const section of HELP_SECTIONS) {
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
          <kbd className="help-kbd">{chord}</kbd>
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
  const [activeSectionName, setActiveSectionName] = useState(HELP_SECTIONS[0].name);
  const [query, setQuery] = useState("");
  const results = useMemo(() => matches(query), [query]);
  const searching = query.trim().length > 0;

  const activeSection =
    HELP_SECTIONS.find((section) => section.name === activeSectionName) ?? HELP_SECTIONS[0];

  return (
    <Dialog name="help" title="Help" className="help-dialog">
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
            {HELP_SECTIONS.map((section) => (
              <button
                key={section.name}
                type="button"
                className={
                  "help-rail-button" +
                  (!searching && section.name === activeSectionName ? " active" : "")
                }
                aria-current={
                  !searching && section.name === activeSectionName ? "page" : undefined
                }
                onClick={() => {
                  setQuery("");
                  setActiveSectionName(section.name);
                }}
              >
                {section.name}
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
    </Dialog>
  );
}
