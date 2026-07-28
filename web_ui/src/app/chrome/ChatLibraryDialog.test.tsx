import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatLibraryDialog } from "./ChatLibraryDialog";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import type { WsTransport } from "../../lib/ws/transport";

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
    ...overrides,
  };
}

const snapshot = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 1,
  rows: [
    row({
      id: 1,
      title: "First Chat",
      preview: "hello there",
      messageCount: 2,
      createdAtIso: "2026-01-15T08:00:00",
      updatedAtIso: "2026-01-15T08:00:00",
    }),
    row({
      id: 2,
      title: "Second Chat",
      preview: "another conversation",
      messageCount: 5,
      createdAtIso: "2026-01-14T08:00:00",
      updatedAtIso: "2026-01-14T08:00:00",
    }),
  ],
  notice: null,
};

function makeTransport() {
  const intents: unknown[][] = [];
  let listener: ((payload: Record<string, unknown>) => void) | null = null;
  const transport = {
    subscribe: (_topic: string, l: (payload: Record<string, unknown>) => void) => {
      listener = l;
      return () => {
        listener = null;
      };
    },
    intent: (topic: string, intent: string, args: unknown[]) => {
      intents.push([topic, intent, args]);
    },
  } as unknown as WsTransport;
  return {
    transport,
    intents,
    push: (payload: Record<string, unknown>) => listener?.(payload),
  };
}

function OpenLibraryButton() {
  const overlays = useOverlays();
  return (
    <button type="button" onClick={() => overlays.open("library", "dialog")}>
      open library
    </button>
  );
}

function setup() {
  const user = userEvent.setup();
  const fake = makeTransport();
  render(
    <OverlayProvider>
      <OpenLibraryButton />
      <ChatLibraryDialog transport={fake.transport} />
    </OverlayProvider>,
  );
  act(() => fake.push(snapshot));
  return { user, ...fake };
}

// "Now" is pinned so date-bucketing is deterministic; only Date is faked so
// userEvent's own internal async scheduling is unaffected.
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-01-15T12:00:00"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("ChatLibraryDialog", () => {
  it("shows a real empty state (not a small message inside an otherwise-normal list) when nothing has been saved", async () => {
    const { user, push } = setup();
    act(() => push({ ...snapshot, rows: [] }));
    await user.click(screen.getByText("open library"));

    expect(screen.getByText("No saved chats yet")).toBeInTheDocument();
    expect(screen.queryByLabelText("Search chats")).toBeNull();
  });

  it("clicking a row's body loads THAT chat and closes the dialog - no separate select-then-Load step", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByRole("button", { name: /Open chat "Second Chat"/ }));

    expect(intents).toContainEqual(["app-chat-library", "loadChat", [2]]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("New Chat dispatches the newChat intent and closes the dialog", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByRole("button", { name: "New Chat" }));
    expect(intents).toContainEqual(["app-chat-library", "newChat", []]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("groups rows under the correct date headers, most recent group first", async () => {
    const { user, push } = setup();
    act(() =>
      push({
        ...snapshot,
        rows: [
          row({ id: 1, title: "Today Chat", updatedAtIso: "2026-01-15T08:00:00", createdAtIso: "2026-01-15T08:00:00" }),
          row({ id: 2, title: "Yesterday Chat", updatedAtIso: "2026-01-14T08:00:00", createdAtIso: "2026-01-14T08:00:00" }),
          row({ id: 3, title: "Week Chat", updatedAtIso: "2026-01-09T08:00:00", createdAtIso: "2026-01-09T08:00:00" }),
          row({ id: 4, title: "Month Chat", updatedAtIso: "2026-01-01T08:00:00", createdAtIso: "2026-01-01T08:00:00" }),
          row({ id: 5, title: "Ancient Chat", updatedAtIso: "2025-11-01T08:00:00", createdAtIso: "2025-11-01T08:00:00" }),
          row({ id: 6, title: "Undated Chat", updatedAtIso: null, createdAtIso: null }),
        ],
      }),
    );
    await user.click(screen.getByText("open library"));

    const headers = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headers).toEqual(["Today", "Yesterday", "Previous 7 Days", "Previous 30 Days", "Older"]);

    const olderSection = screen.getByRole("region", { name: "Older" });
    // Undated rows sort AFTER dated Older rows, never guessed into a
    // recent bucket just because they have no timestamp.
    const olderTitles = within(olderSection)
      .getAllByRole("button", { name: /^Open chat/ })
      .map((b) => b.textContent);
    expect(olderTitles[0]).toContain("Ancient Chat");
    expect(olderTitles[1]).toContain("Undated Chat");
  });

  it("an empty preview renders a placeholder instead of a blank line", async () => {
    const { user, push } = setup();
    act(() => push({ ...snapshot, rows: [row({ id: 9, title: "Blank Chat", preview: "" })] }));
    await user.click(screen.getByText("open library"));

    expect(screen.getByText("No messages yet")).toBeInTheDocument();
  });

  it("search filters live against title and preview text", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open library"));

    await user.type(screen.getByLabelText("Search chats"), "another");

    expect(screen.queryByText("First Chat")).toBeNull();
    expect(screen.getByText("Second Chat")).toBeInTheDocument();
  });

  it("shows a distinct 'no matches' state (not the empty-library state) for a search with no results, and Clear search restores the list", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open library"));

    await user.type(screen.getByLabelText("Search chats"), "nothing matches this");
    expect(screen.getByText('No chats match "nothing matches this".')).toBeInTheDocument();
    expect(screen.queryByText("No saved chats yet")).toBeNull();

    await user.click(screen.getByText("Clear search"));
    expect(screen.getByText("First Chat")).toBeInTheDocument();
  });

  it("renaming a row: pencil opens an inline input, Enter commits renameChat with the trimmed title", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Rename "First Chat"'));
    const input = screen.getByLabelText('Rename "First Chat"') as HTMLInputElement;
    expect(input.value).toBe("First Chat");

    await user.clear(input);
    await user.type(input, "  Renamed Title  {Enter}");

    expect(intents).toContainEqual(["app-chat-library", "renameChat", [1, "Renamed Title"]]);
    // The dialog stays open for a rename (unlike load/new).
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("renaming a row: the Cancel (x) button reverts without dispatching renameChat", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Rename "First Chat"'));
    await user.click(screen.getByLabelText("Cancel rename"));

    expect(intents.filter((i) => i[1] === "renameChat")).toEqual([]);
    expect(screen.getByText("First Chat")).toBeInTheDocument();
  });

  it("Escape while renaming closes the whole dialog (the app's global Escape-to-close policy) without committing a rename", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Rename "First Chat"'));
    await user.keyboard("{Escape}");

    expect(intents.filter((i) => i[1] === "renameChat")).toEqual([]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("deleting a row: trash opens an inline scoped confirm, confirming dispatches deleteChat for THAT row only", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Delete "First Chat"'));
    expect(screen.getByText("Delete?")).toBeInTheDocument();
    // The other row is untouched - no confirm state leaked onto it.
    expect(screen.getByLabelText('Delete "Second Chat"')).toBeInTheDocument();

    await user.click(screen.getByLabelText('Confirm delete "First Chat"'));
    expect(intents).toContainEqual(["app-chat-library", "deleteChat", [1]]);
  });

  it("deleting a row: Cancel dismisses the confirm without dispatching deleteChat", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Delete "First Chat"'));
    await user.click(screen.getByLabelText("Cancel delete"));

    expect(intents.filter((i) => i[1] === "deleteChat")).toEqual([]);
    expect(screen.getByLabelText('Delete "First Chat"')).toBeInTheDocument();
  });

  it("a DB-read notice still renders when present", async () => {
    const { user, push } = setup();
    act(() => push({ ...snapshot, notice: "Could not load saved chats: disk error" }));
    await user.click(screen.getByText("open library"));

    expect(screen.getByText("Could not load saved chats: disk error")).toBeInTheDocument();
  });
});
