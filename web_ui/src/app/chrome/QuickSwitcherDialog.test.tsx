import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { QuickSwitcherDialog } from "./QuickSwitcherDialog";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import type { WsTransport } from "../../lib/ws/transport";

/**
 * ADR-020 stage 20.5. Mirrors two established precedents rather than
 * inventing test shape:
 * - GlobalSearchDialog.test.tsx's own makeTransport() (subscribe/fireIntent
 *   double, no real WsTransport).
 * - CommandPalette.test.tsx's own ArrowDown/Enter + scrollIntoView-stub
 *   coverage, since this dialog shares that exact keyboard contract.
 */

function row(overrides: Record<string, unknown>) {
  return {
    id: 1,
    title: "Untitled",
    createdLabel: "Jan 01, 2026",
    updatedLabel: "Jan 01, 2026",
    createdAtIso: "2026-01-01T08:00:00",
    updatedAtIso: "2026-01-01T08:00:00",
    preview: "",
    messageCount: 0,
    workspaceId: 1,
    favorite: false,
    archived: false,
    tags: [] as string[],
    ...overrides,
  };
}

function workspace(overrides: Record<string, unknown>) {
  return { id: 1, name: "Default", icon: "", archived: false, defaultModelProvider: "", defaultModelId: "", ...overrides };
}

function makeTransport() {
  const intents: unknown[][] = [];
  const resubscribes: string[] = [];
  const listeners = new Map<string, (payload: unknown) => void>();
  const transport = {
    subscribe: (topic: string, listener: (payload: unknown) => void) => {
      listeners.set(topic, listener);
      return () => listeners.delete(topic);
    },
    // ADR-020 stage 20.5: QuickSwitcherDialog.tsx calls this unconditionally
    // right after subscribe() - see that file's own comment for why.
    resubscribe: (topic: string) => {
      resubscribes.push(topic);
    },
    intent: () => {},
    fireIntent: (topic: string, intent: string, args: unknown[] = []) => {
      intents.push([topic, intent, args]);
    },
  } as unknown as WsTransport;
  return {
    transport,
    intents,
    resubscribes,
    push: (payload: Record<string, unknown>) => listeners.get("app-chat-library")?.(payload),
  };
}

function OpenQuickSwitcherButton() {
  const overlays = useOverlays();
  return (
    <button type="button" onClick={() => overlays.toggle("quick-switcher", "dialog")}>
      open quick switcher
    </button>
  );
}

function setup() {
  const user = userEvent.setup();
  const fake = makeTransport();
  render(
    <OverlayProvider>
      <OpenQuickSwitcherButton />
      <QuickSwitcherDialog transport={fake.transport} />
    </OverlayProvider>,
  );
  return { user, ...fake };
}

const SNAPSHOT = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 1,
  rows: [
    row({ id: 1, title: "Recent Graph", workspaceId: 1, updatedLabel: "Today" }),
    row({ id: 2, title: "Older Graph", workspaceId: 2, updatedLabel: "Yesterday" }),
    row({ id: 3, title: "Archived Graph", workspaceId: 1, archived: true, updatedLabel: "Last week" }),
  ],
  workspaces: [workspace({ id: 1, name: "Default" }), workspace({ id: 2, name: "Work" })],
  notice: null,
};

describe("QuickSwitcherDialog", () => {
  it("is closed until the surface opens", () => {
    setup();
    expect(screen.queryByLabelText("Search graphs by title")).toBeNull();
  });

  it("shows non-archived graphs in the backend's own (recency) order when the query is blank", async () => {
    const { user, push } = setup();
    push(SNAPSHOT);
    await user.click(screen.getByText("open quick switcher"));

    const options = screen.getAllByRole("option");
    expect(options.map((el) => el.textContent)).toEqual([
      "Recent GraphDefault · Today",
      "Older GraphWork · Yesterday",
    ]);
    expect(screen.queryByText("Archived Graph")).toBeNull();
  });

  it("fuzzy-filters by typed title text", async () => {
    const { user, push } = setup();
    push(SNAPSHOT);
    await user.click(screen.getByText("open quick switcher"));

    await user.type(screen.getByLabelText("Search graphs by title"), "older");

    expect(screen.getByText("Older Graph")).toBeInTheDocument();
    expect(screen.queryByText("Recent Graph")).toBeNull();
  });

  it("shows a 'no matching graphs' message when nothing matches", async () => {
    const { user, push } = setup();
    push(SNAPSHOT);
    await user.click(screen.getByText("open quick switcher"));

    await user.type(screen.getByLabelText("Search graphs by title"), "zzz-nonexistent");

    expect(screen.getByText("No matching graphs")).toBeInTheDocument();
  });

  it("clicking a row fires loadChat with its id and closes the dialog", async () => {
    const { user, push, intents } = setup();
    push(SNAPSHOT);
    await user.click(screen.getByText("open quick switcher"));

    await user.click(screen.getByText("Older Graph"));

    expect(intents).toContainEqual(["app-chat-library", "loadChat", [2]]);
    expect(screen.queryByLabelText("Search graphs by title")).toBeNull();
  });

  it("ArrowDown/Enter selects and loads the highlighted graph", async () => {
    const { user, push, intents } = setup();
    push(SNAPSHOT);
    await user.click(screen.getByText("open quick switcher"));

    await user.keyboard("{ArrowDown}{Enter}");

    expect(intents).toContainEqual(["app-chat-library", "loadChat", [2]]);
    expect(screen.queryByLabelText("Search graphs by title")).toBeNull();
  });

  it("resets the query on each fresh open", async () => {
    const { user, push } = setup();
    push(SNAPSHOT);
    await user.click(screen.getByText("open quick switcher"));
    await user.type(screen.getByLabelText("Search graphs by title"), "older");
    await user.keyboard("{Escape}");

    await user.click(screen.getByText("open quick switcher"));

    expect(screen.getByLabelText("Search graphs by title")).toHaveValue("");
    expect(screen.getByText("Recent Graph")).toBeInTheDocument();
  });

  it("regression: resubscribes to app-chat-library on mount, since it is eagerly mounted and would otherwise silently claim the topic's one-time fresh-snapshot slot from a later subscriber (ChatLibraryDialog.tsx)", () => {
    const { resubscribes } = setup();

    expect(resubscribes).toContain("app-chat-library");
  });
});
