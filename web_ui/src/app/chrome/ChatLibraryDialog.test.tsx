import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ChatLibraryDialog } from "./ChatLibraryDialog";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import type { WsTransport } from "../../lib/ws/transport";

const snapshot = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 1,
  rows: [
    { id: 1, title: "First Chat", createdLabel: "Jan 01, 2026", updatedLabel: "Jan 02, 2026" },
    { id: 2, title: "Second Chat", createdLabel: "Jan 03, 2026", updatedLabel: "Jan 04, 2026" },
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

describe("ChatLibraryDialog", () => {
  it("disables Load Chat when there are no saved chats at all", async () => {
    const { user, push } = setup();
    act(() => push({ ...snapshot, rows: [] }));
    await user.click(screen.getByText("open library"));

    expect(screen.getByText("Load Chat")).toBeDisabled();
  });

  it("Load Chat is enabled once a row is selectable, dispatches loadChat with the right id, and closes the dialog", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));

    // The first row is the default effective selection.
    const loadButton = screen.getByText("Load Chat");
    expect(loadButton).not.toBeDisabled();

    await user.click(loadButton);
    expect(intents).toContainEqual(["app-chat-library", "loadChat", [1]]);
    // Dialog closes immediately on Load Chat, matching legacy's own behavior.
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("loadChat targets whichever row is explicitly selected, not always the first", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByText("Second Chat"));
    await user.click(screen.getByText("Load Chat"));
    expect(intents).toContainEqual(["app-chat-library", "loadChat", [2]]);
  });

  it("New Chat stays disabled - no session SAVE primitive exists until R6.5", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open library"));

    expect(screen.getByText("New Chat")).toBeDisabled();
  });
});
