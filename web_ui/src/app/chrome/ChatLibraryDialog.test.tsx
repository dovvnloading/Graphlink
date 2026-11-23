import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { ChatLibraryDialog } from "./ChatLibraryDialog";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import type { WsTransport } from "../../lib/ws/transport";
import { initialSceneState, type SceneStore } from "../canvas/sceneStore";

function row(overrides: Record<string, unknown> = {}) {
  return {
    id: 1, title: "First chat", createdLabel: "Today", updatedLabel: "Today",
    createdAtIso: "2026-01-15T08:00:00", updatedAtIso: "2026-01-15T08:00:00",
    preview: "A first preview", messageCount: 2, workspaceId: 1,
    favorite: false, archived: false, tags: [] as string[], ...overrides,
  };
}
function workspace(overrides: Record<string, unknown> = {}) {
  return { id: 1, name: "Default", icon: "", archived: false,
    defaultModelProvider: "", defaultModelId: "", ...overrides };
}
const SNAPSHOT = {
  schemaVersion: 1, minCompatibleSchemaVersion: 1, revision: 1,
  rows: [
    row({ id: 1, title: "First chat", favorite: true, tags: ["design"] }),
    row({ id: 2, title: "Second chat", preview: "Another conversation", messageCount: 5,
      updatedAtIso: "2026-01-14T08:00:00", workspaceId: 2 }),
    row({ id: 3, title: "Archived chat", archived: true, updatedAtIso: "2025-11-01T08:00:00" }),
  ],
  workspaces: [workspace(), workspace({ id: 2, name: "Work" })],
  notice: null,
};

function makeTransport() {
  const intents: unknown[][] = [];
  const resubscribes: string[] = [];
  const listeners = new Map<string, (payload: Record<string, unknown>) => void>();
  const request = vi.fn<(topic: string, intent: string, args?: unknown[]) => Promise<unknown>>().mockResolvedValue(true);
  const transport = {
    subscribe: (topic: string, listener: (payload: Record<string, unknown>) => void) => {
      listeners.set(topic, listener);
      return () => { listeners.delete(topic); };
    },
    resubscribe: (topic: string) => { resubscribes.push(topic); },
    fireIntent: (topic: string, intent: string, args: unknown[] = []) => {
      intents.push([topic, intent, args]);
    },
    request: (topic: string, intent: string, args: unknown[] = []) => {
      intents.push([topic, intent, args]);
      return request(topic, intent, args);
    },
  } as unknown as WsTransport;
  return {
    transport, intents, resubscribes, request,
    push: (snapshot: Record<string, unknown>) => listeners.get("app-chat-library")?.(snapshot),
  };
}

function OpenLibraryButton() {
  const overlays = useOverlays();
  return <button type="button" onClick={() => overlays.open("library", "dialog")}>open library</button>;
}

function setup(dirty = false) {
  const user = userEvent.setup();
  const fake = makeTransport();
  const cancelWorkspacePreview = vi.fn();
  const store = {
    cancelWorkspacePreview,
    getScene: () => ({ ...initialSceneState, nodes: dirty ? [{ id: "dirty" }] : [] }),
  } as unknown as SceneStore;
  render(
    <OverlayProvider>
      <OpenLibraryButton />
      <ChatLibraryDialog transport={fake.transport} store={store} />
      <ConfirmDialog />
    </OverlayProvider>,
  );
  act(() => fake.push(SNAPSHOT));
  return { user, ...fake, cancelWorkspacePreview };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-01-15T12:00:00"));
});
afterEach(() => { vi.useRealTimers(); });

describe("ChatLibraryDialog on the kit", () => {
  it("renders the kit dialog, live search, filters, workspace picker, and overline groups", async () => {
    const { user, resubscribes } = setup();
    await user.click(screen.getByText("open library"));
    expect(screen.getByRole("dialog", { name: "Chat library" })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search chats" })).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: "Filter chats" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Workspace" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Today" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Yesterday" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Earlier" })).toBeNull();
    expect(resubscribes).toContain("app-chat-library");
  });

  it("keeps stable accessible open-chat names and opens a chosen chat", async () => {
    const { user, intents, cancelWorkspacePreview } = setup();
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("button", { name: /Open chat "Second chat", 5 messages, updated/ }));
    expect(intents).toContainEqual(["app-chat-library", "loadChat", [2]]);
    expect(cancelWorkspacePreview).toHaveBeenCalledOnce();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("groups all historical dates under Earlier", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("radio", { name: "Archived" }));
    expect(within(screen.getByRole("region", { name: "Earlier" })).getByText("Archived chat")).toBeInTheDocument();
  });

  it("filters by text, by starred/archived status, and clears the query", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open library"));
    await user.type(screen.getByRole("searchbox", { name: "Search chats" }), "Another");
    expect(screen.getByText("Second chat")).toBeInTheDocument();
    expect(screen.queryByText("First chat")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Clear Search chats" }));
    await user.click(screen.getByRole("radio", { name: "Starred" }));
    expect(screen.getByText("First chat")).toBeInTheDocument();
    expect(screen.queryByText("Second chat")).toBeNull();
    await user.click(screen.getByRole("radio", { name: "Archived" }));
    expect(screen.getByText("Archived chat")).toBeInTheDocument();
  });

  it("shows distinct kit EmptyStates for an empty library and an empty result", async () => {
    const { user, push } = setup();
    await user.click(screen.getByText("open library"));
    await user.type(screen.getByRole("searchbox", { name: "Search chats" }), "no match");
    expect(screen.getByText("No matching chats")).toBeInTheDocument();
    act(() => push({ ...SNAPSHOT, revision: 2, rows: [] }));
    expect(screen.getByText("No saved chats yet")).toBeInTheDocument();
  });

  it("selects a workspace and preserves the workspaceId when starting a new chat", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("button", { name: "Workspace" }));
    await user.click(within(await screen.findByRole("listbox", { name: "Workspace" })).getByRole("option", { name: "Work" }));
    expect(screen.getByText("Second chat")).toBeInTheDocument();
    expect(screen.queryByText("First chat")).toBeNull();
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(intents).toContainEqual(["app-chat-library", "newChat", [2]]);
  });

  it("supports F2 rename, Enter save, and Escape cancel without dismissing the library", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    const open = screen.getByRole("button", { name: /Open chat "First chat"/ });
    open.focus();
    await user.keyboard("{F2}");
    const field = screen.getByRole("textbox", { name: 'Rename "First chat"' });
    await user.clear(field);
    await user.type(field, "  Edited chat  {Enter}");
    await waitFor(() => expect(intents).toContainEqual(["app-chat-library", "renameChat", [1, "Edited chat"]]));
    await user.click(screen.getByRole("button", { name: 'Rename "First chat"' }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("textbox", { name: 'Rename "First chat"' })).toBeNull();
    expect(screen.getByRole("dialog", { name: "Chat library" })).toBeInTheDocument();
  });

  it("uses arrow keys to move among chat rows", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open library"));
    screen.getByRole("button", { name: /Open chat "First chat"/ }).focus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("button", { name: /Open chat "Second chat"/ })).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(screen.getByRole("button", { name: /Open chat "First chat"/ })).toHaveFocus();
  });

  it("stars rows through ToggleButton and archives them through the kit menu", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("button", { name: 'Remove "First chat" from favorites' }));
    expect(intents).toContainEqual(["app-chat-library", "setGraphFavorite", [1, false]]);
    await user.click(screen.getAllByRole("button", { name: "More" })[0]);
    await user.click(await screen.findByRole("menuitem", { name: "Archive" }));
    expect(intents).toContainEqual(["app-chat-library", "setGraphArchived", [1, true]]);
  });

  it("edits comma-separated tags from More without losing other tags", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    await user.click(screen.getAllByRole("button", { name: "More" })[0]);
    await user.click(await screen.findByRole("menuitem", { name: "Tag…" }));
    const field = screen.getByRole("textbox", { name: 'Edit tags for "First chat"' });
    await user.clear(field);
    await user.type(field, "docs, release{Enter}");
    expect(intents).toContainEqual(["app-chat-library", "setGraphTags", [1, ["docs", "release"]]]);
  });

  it("Delete key opens a nested destructive ConfirmDialog and honours cancel", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    screen.getByRole("button", { name: /Open chat "First chat"/ }).focus();
    await user.keyboard("{Delete}");
    expect(await screen.findByRole("dialog", { name: "Delete chat?" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(intents.some((intent) => intent[1] === "deleteChat")).toBe(false);
    screen.getByRole("button", { name: /Open chat "First chat"/ }).focus();
    await user.keyboard("{Delete}");
    await user.click(await screen.findByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(intents).toContainEqual(["app-chat-library", "deleteChat", [1]]));
  });

  it("requires confirmation to navigate away from a populated scene", async () => {
    const { user, intents, cancelWorkspacePreview } = setup(true);
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("button", { name: /Open chat "First chat"/ }));
    expect(await screen.findByRole("dialog", { name: "Open another chat?" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(intents.some((intent) => intent[1] === "loadChat")).toBe(false);
    expect(cancelWorkspacePreview).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /Open chat "First chat"/ }));
    await user.click(await screen.findByRole("button", { name: "Continue" }));
    await waitFor(() => expect(intents).toContainEqual(["app-chat-library", "loadChat", [1]]));
  });

  it("uses a second confirmation to discard an incomplete live copy", async () => {
    const { user, request, intents } = setup();
    request.mockResolvedValueOnce("discard-required").mockResolvedValueOnce(true);
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("button", { name: /Open chat "First chat"/ }));
    expect(await screen.findByRole("dialog", { name: "Discard incomplete chat?" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Discard incomplete copy" }));
    await waitFor(() => expect(intents.filter((entry) => entry[1] === "loadChat")).toEqual([
      ["app-chat-library", "loadChat", [1]],
      ["app-chat-library", "loadChat", [1, true]],
    ]));
  });

  it("manages workspaces without altering the graph and keeps the import/export intents", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("button", { name: "Workspace" }));
    await user.click(within(await screen.findByRole("listbox", { name: "Workspace" })).getByRole("option", { name: "Manage workspaces…" }));
    await user.click(screen.getByRole("button", { name: "Create workspace" }));
    await user.type(screen.getByRole("textbox", { name: "New workspace name" }), "Writing");
    await user.click(screen.getByRole("button", { name: "Create workspace" }));
    expect(intents).toContainEqual(["app-chat-library", "createWorkspace", ["Writing"]]);
    await user.click(screen.getByRole("button", { name: "Default" }));
    await user.click(screen.getByRole("button", { name: "Export workspace…" }));
    expect(intents).toContainEqual(["app-chat-library", "exportWorkspace", [1]]);
    await user.click(screen.getByRole("button", { name: "Import workspace…" }));
    expect(intents).toContainEqual(["app-chat-library", "importWorkspace", [1]]);
  });
});
