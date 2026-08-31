import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { HelpDialog } from "./HelpDialog";
import { HELP_SECTIONS } from "./help-data/sections";
import { resolveShortcut, type ShortcutId, type ShortcutKeyEvent } from "./shortcuts";
import { OverlayProvider, useOverlays } from "../overlays/overlays";

/**
 * The Help panel had no tests at all, which is how it drifted: an audit
 * found it describing a "Controls toggle" renamed to View, a Shift-drag
 * zoom gesture that had become rubber-band selection, and a plugin under a
 * name the picker no longer used. Prose cannot be type-checked, so most of
 * that class of rot is only catchable by a human reading the app and the
 * copy side by side.
 *
 * One slice of it CAN be pinned mechanically, and it is the slice readers
 * trust most literally: the key chords. The suite below parses every chord
 * out of the content and feeds it to the real resolveShortcut() - the same
 * function GlobalShortcuts dispatches on - so the day a binding moves, Help
 * fails a test instead of lying to somebody.
 */

function OpenHelpOnMount({ children }: { children: React.ReactNode }) {
  const overlays = useOverlays();
  if (!overlays.isOpen("help")) overlays.open("help", "dialog");
  return <>{children}</>;
}

function renderOpen() {
  return render(
    <OverlayProvider>
      <OpenHelpOnMount>
        <HelpDialog />
      </OpenHelpOnMount>
    </OverlayProvider>,
  );
}

const allItems = HELP_SECTIONS.flatMap((section) =>
  section.subsections.flatMap((subsection) =>
    subsection.items.map((item) => ({ section, subsection, item })),
  ),
);

/** "Ctrl+Shift+Z" -> the event GlobalShortcuts would see. */
function chordToEvent(chord: string): ShortcutKeyEvent {
  const parts = chord.split("+").map((p) => p.trim());
  const key = parts[parts.length - 1];
  const named: Record<string, string> = { "←": "ArrowLeft", "→": "ArrowRight", "↑": "ArrowUp", "↓": "ArrowDown" };
  return {
    key: named[key] ?? key,
    ctrlKey: parts.includes("Ctrl"),
    metaKey: false,
    shiftKey: parts.includes("Shift"),
    altKey: parts.includes("Alt"),
  };
}

describe("Help content", () => {
  it("has no empty or placeholder copy anywhere", () => {
    expect(allItems.length).toBeGreaterThan(40);
    for (const { section, subsection, item } of allItems) {
      const where = `${section.name} / ${subsection.title} / ${item.action}`;
      expect(section.name.trim(), where).not.toBe("");
      expect(section.description.trim(), where).not.toBe("");
      expect(subsection.title.trim(), where).not.toBe("");
      expect(item.action.trim(), where).not.toBe("");
      // A one- or two-word description is a placeholder, not an
      // explanation. The floor is deliberately low: entries in the Keyboard
      // table are terse on purpose ("Your saved graphs."), and padding them
      // out to satisfy a word count would make the reference worse.
      expect(item.description.trim().split(/\s+/).length, where).toBeGreaterThan(2);
    }
  });

  it("has unique section names, since the rail addresses sections by name", () => {
    const names = HELP_SECTIONS.map((s) => s.name);
    expect(new Set(names).size).toBe(names.length);
  });

  // The whole point of this file. Every chord printed in Help is resolved
  // through the real dispatcher; an unrecognised chord means the content
  // promises a key the app does not answer to.
  it("every key chord it documents is a real, live binding", () => {
    const chords = allItems.flatMap(({ item }) => item.keys ?? []);
    expect(chords.length).toBeGreaterThan(10);
    for (const chord of chords) {
      expect(resolveShortcut(chordToEvent(chord)), `${chord} resolves to nothing`).not.toBeNull();
    }
  });

  it("documents every binding the app actually has", () => {
    const documented = new Set(
      allItems.flatMap(({ item }) => (item.keys ?? []).map((c) => resolveShortcut(chordToEvent(c)))),
    );
    // Arrow navigation is documented as a pair (Ctrl+left / Ctrl+right)
    // standing for all four directions, which is how a person reads it -
    // so the vertical pair is exempt rather than spelled out twice.
    const expected: ShortcutId[] = [
      "new-chat", "toggle-library", "save-chat", "create-frame", "create-container",
      "compare-branches", "synthesize-branches", "toggle-palette", "toggle-search",
      "toggle-quick-switcher", "navigate-left", "navigate-right", "undo", "redo",
    ];
    for (const id of expected) {
      expect(documented.has(id), `no Help entry documents "${id}"`).toBe(true);
    }
  });

  it("names plugins as the picker names them", () => {
    const copy = JSON.stringify(HELP_SECTIONS);
    // Renamed in the app and left stale here for at least one release.
    expect(copy).not.toContain("Graphlink-Web");
    for (const name of ["Web Research", "Gitlink", "Virtual Environment Runner", "HTML Renderer", "System Prompt", "Conversation Node", "Artifact / Drafter"]) {
      expect(copy, `${name} is missing from Help`).toContain(name);
    }
  });

  it("does not describe controls that were renamed or removed", () => {
    const copy = JSON.stringify(HELP_SECTIONS);
    // "Controls" became the View panel; Shift-drag became rubber-band
    // selection rather than a zoom-to-area gesture.
    expect(copy).not.toContain("Controls toggle");
    expect(copy).not.toMatch(/zoom the view to a specific region/i);
  });
});

describe("HelpDialog", () => {
  it("opens on the first section with its rail", () => {
    renderOpen();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(HELP_SECTIONS[0].name);
    expect(screen.getByRole("navigation", { name: "Help sections" })).toBeInTheDocument();
    for (const section of HELP_SECTIONS) {
      expect(screen.getByRole("button", { name: section.name })).toBeInTheDocument();
    }
  });

  it("switches sections from the rail", async () => {
    const user = userEvent.setup();
    renderOpen();
    await user.click(screen.getByRole("button", { name: "Keyboard" }));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Keyboard");
  });

  it("renders documented chords as <kbd>, not as prose in the title", () => {
    renderOpen();
    // Start Here documents the palette; the chord is an element, so it can
    // be found as one rather than by parsing a heading string.
    const kbds = document.querySelectorAll("kbd.help-kbd");
    expect(kbds.length).toBeGreaterThan(0);
    expect([...kbds].map((k) => k.textContent)).toContain("Ctrl+K");
  });

  describe("search", () => {
    it("finds an entry by a word in its description, across sections", async () => {
      const user = userEvent.setup();
      renderOpen();
      await user.type(screen.getByRole("searchbox", { name: "Search help" }), "minimap");
      const results = screen.getByRole("region", { name: "Help search results" });
      expect(within(results).getByText(/Click anywhere in it to jump there/)).toBeInTheDocument();
    });

    it("finds a shortcut by its chord, which is what people actually type", async () => {
      const user = userEvent.setup();
      renderOpen();
      await user.type(screen.getByRole("searchbox", { name: "Search help" }), "ctrl+p");
      const results = screen.getByRole("region", { name: "Help search results" });
      // Two entries name it - the Finding Things explainer and the Keyboard
      // table row - and both are legitimate hits for the chord.
      expect(within(results).getAllByText("Quick switcher").length).toBeGreaterThan(0);
    });

    it("replaces the section view rather than filtering inside it", async () => {
      const user = userEvent.setup();
      renderOpen();
      await user.type(screen.getByRole("searchbox", { name: "Search help" }), "autopilot");
      // The Builder's section is not selected, but its entry is reachable.
      expect(screen.queryByRole("region", { name: HELP_SECTIONS[0].name })).toBeNull();
      const results = screen.getByRole("region", { name: "Help search results" });
      expect(within(results).getByText(/Co-pilot or Autopilot/)).toBeInTheDocument();
      // Each result says where it came from, since there are no headings to
      // sit under in a flat list.
      expect(within(results).getAllByText(/The Builder · /).length).toBeGreaterThan(0);
    });

    it("says so plainly when nothing matches", async () => {
      const user = userEvent.setup();
      renderOpen();
      await user.type(screen.getByRole("searchbox", { name: "Search help" }), "zzzznope");
      expect(screen.getByText('Nothing matches "zzzznope".')).toBeInTheDocument();
    });

    it("choosing a section clears the query and goes back to browsing", async () => {
      const user = userEvent.setup();
      renderOpen();
      await user.type(screen.getByRole("searchbox", { name: "Search help" }), "budget");
      await user.click(screen.getByRole("button", { name: "The Agent" }));
      expect(screen.getByRole("searchbox", { name: "Search help" })).toHaveValue("");
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("The Agent");
    });

    it("Escape clears the query before it closes the dialog", async () => {
      const user = userEvent.setup();
      renderOpen();
      const box = screen.getByRole("searchbox", { name: "Search help" });
      await user.type(box, "grid");
      await user.keyboard("{Escape}");
      expect(box).toHaveValue("");
      // Still open: the first Escape was spent on the query.
      expect(screen.getByRole("navigation", { name: "Help sections" })).toBeInTheDocument();
    });
  });
});
