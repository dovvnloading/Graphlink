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
    push: (snapshot: Record<string, unknown>, topic = "app-chat-library") => listeners.get(topic)?.(snapshot),
  };
}

function composerSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    schemaVersion: 1,
    minCompatibleSchemaVersion: 1,
    revision: 1,
    draft: { id: "", text: "", contextMode: "branch", sendMode: "enter_to_send" },
    context: { anchor: null, items: [], totalTokens: 0, reviewAvailable: false },
    route: {
      mode: "ollama",
      provider: "Ollama (Local)",
      modelId: "",
      modelLabel: "",
      modelOptions: [
        { id: "llama3", label: "Llama 3" },
        { id: "mistral", label: "Mistral" },
      ],
      reasoning: { level: "off", label: "Off", options: [] },
      label: "Ollama (Local)",
      available: true,
      canChange: true,
    },
    request: { id: null, state: "idle", message: "", canSend: true, canCancel: false, canRetry: false },
    capabilities: {
      attachments: false,
      contextReview: false,
      routeSelection: false,
      modelSelection: true,
      reasoningSelection: true,
      settingsShortcut: true,
      cancellation: false,
    },
    ...overrides,
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


async function chooseWorkspace(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole("button", { name: "Workspace" }));
  await user.click(within(await screen.findByRole("listbox", { name: "Workspace" }))
    .getByRole("option", { name }));
}

async function openWorkspaceManager(user: ReturnType<typeof userEvent.setup>) {
  await chooseWorkspace(user, "Manage workspaces…");
}

function actionsFor(title: string) {
  const row = screen.getByRole("button", { name: new RegExp('Open chat "' + title + '"') }).closest("li");
  if (!row) throw new Error("Chat row missing: " + title);
  return within(row);
}

async function chooseRowAction(user: ReturnType<typeof userEvent.setup>, title: string, action: string) {
  await user.click(actionsFor(title).getByRole("button", { name: "More" }));
  await user.click(await screen.findByRole("menuitem", { name: action }));
}

async function startDelete(user: ReturnType<typeof userEvent.setup>, title: string) {
  await chooseRowAction(user, title, "Delete…");
  return screen.findByRole("dialog", { name: "Delete chat?" });
}

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

  it("empty library has an actual EmptyState rather than an empty list", async () => {
    const { user, push } = setup();
    act(() => push({ ...SNAPSHOT, revision: 2, rows: [] }));
    await user.click(screen.getByText("open library"));
    expect(screen.getByText("No saved chats yet")).toBeInTheDocument();
    expect(screen.queryByRole("list")).toBeNull();
    expect(screen.queryByText("No matching chats")).toBeNull();
  });

  it("clicking the row body loads that exact chat immediately, with no Load step", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    await user.click(actionsFor("Second chat").getByRole("button", { name: /Open chat/ }));
    await waitFor(() => expect(intents).toContainEqual(["app-chat-library", "loadChat", [2]]));
    expect(screen.queryByRole("button", { name: /^Load$/ })).toBeNull();
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Chat library" })).toBeNull());
  });

  it("declining a dirty-scene chat switch leaves the library open and never navigates", async () => {
    const { user, intents, cancelWorkspacePreview } = setup(true);
    await user.click(screen.getByText("open library"));
    await user.click(actionsFor("Second chat").getByRole("button", { name: /Open chat/ }));
    expect(await screen.findByRole("dialog", { name: "Open another chat?" })).toBeInTheDocument();
    expect(intents.some((entry) => entry[1] === "loadChat")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("dialog", { name: "Chat library" })).toBeInTheDocument();
    expect(cancelWorkspacePreview).not.toHaveBeenCalled();
    expect(intents.some((entry) => entry[1] === "loadChat")).toBe(false);
  });

  it("asks before a new chat from a populated canvas and does not dispatch before consent", async () => {
    const { user, intents, cancelWorkspacePreview } = setup(true);
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(await screen.findByRole("dialog", { name: "Start a new chat?" })).toBeInTheDocument();
    expect(intents.some((entry) => entry[1] === "newChat")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(intents).toContainEqual(["app-chat-library", "newChat", []]));
    expect(cancelWorkspacePreview).toHaveBeenCalledOnce();
  });

  it("New chat on All sends no workspaceId and closes the dialog", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("button", { name: "New chat" }));
    await waitFor(() => expect(intents).toContainEqual(["app-chat-library", "newChat", []]));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Chat library" })).toBeNull());
  });

  it("declining the second incomplete-copy confirmation never retries loading", async () => {
    const { user, request, intents } = setup();
    request.mockResolvedValueOnce("discard-required");
    await user.click(screen.getByText("open library"));
    await user.click(actionsFor("First chat").getByRole("button", { name: /Open chat/ }));
    expect(await screen.findByRole("dialog", { name: "Discard incomplete chat?" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(intents.filter((item) => item[1] === "loadChat")).toEqual([
      ["app-chat-library", "loadChat", [1]],
    ]);
    expect(screen.getByRole("dialog", { name: "Chat library" })).toBeInTheDocument();
  });

  it("New chat without a selected workspace requires a distinct incomplete-copy confirmation", async () => {
    const { user, request, intents } = setup();
    request.mockResolvedValueOnce("discard-required").mockResolvedValueOnce(true);
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("button", { name: "New chat" }));
    const dialog = await screen.findByRole("dialog", { name: "Discard incomplete chat?" });
    expect(within(dialog).getByText(/original saved chat/i)).toBeInTheDocument();
    expect(intents.filter((item) => item[1] === "newChat")).toEqual([
      ["app-chat-library", "newChat", []],
    ]);
    await user.click(within(dialog).getByRole("button", { name: "Discard incomplete copy" }));
    await waitFor(() => expect(intents.filter((item) => item[1] === "newChat")).toEqual([
      ["app-chat-library", "newChat", []],
      ["app-chat-library", "newChat", [null, true]],
    ]));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Chat library" })).toBeNull());
  });

  it("date groups are newest first and undated chats sort last", async () => {
    const { user, push } = setup();
    act(() => push({ ...SNAPSHOT, revision: 2, rows: [
      row({ id: 11, title: "Undated", updatedAtIso: null, createdAtIso: null }),
      row({ id: 12, title: "Old", updatedAtIso: "2025-12-01T08:00:00" }),
      row({ id: 13, title: "Yesterday row", updatedAtIso: "2026-01-14T08:00:00" }),
      row({ id: 14, title: "Today row", updatedAtIso: "2026-01-15T08:00:00" }),
    ] }));
    await user.click(screen.getByText("open library"));
    expect(screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent))
      .toEqual(["Today", "Yesterday", "Earlier"]);
    const titles = within(screen.getByRole("region", { name: "Earlier" }))
      .getAllByRole("button", { name: /^Open chat/ }).map((button) => button.textContent);
    expect(titles[0]).toContain("Old");
    expect(titles[1]).toContain("Undated");
  });

  it("uses a visible placeholder for a chat with no preview", async () => {
    const { user, push } = setup();
    act(() => push({ ...SNAPSHOT, revision: 2, rows: [row({ title: "No preview", preview: "" })] }));
    await user.click(screen.getByText("open library"));
    expect(screen.getByText("No messages yet")).toBeInTheDocument();
  });

  it("live search matches titles as well as preview text", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open library"));
    const search = screen.getByRole("searchbox", { name: "Search chats" });
    await user.type(search, "FIRST");
    expect(screen.getByText("First chat")).toBeInTheDocument();
    expect(screen.queryByText("Second chat")).toBeNull();
    await user.clear(search);
    await user.type(search, "Another conversation");
    expect(screen.getByText("Second chat")).toBeInTheDocument();
    expect(screen.queryByText("First chat")).toBeNull();
  });

  it("search no-matches differs from an empty library and Clear search restores results", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open library"));
    await user.type(screen.getByRole("searchbox", { name: "Search chats" }), "unlikely phrase");
    expect(screen.getByText("No matching chats")).toBeInTheDocument();
    expect(screen.queryByText("No saved chats yet")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Clear search" }));
    expect(screen.getByText("First chat")).toBeInTheDocument();
    expect(screen.getByText("Second chat")).toBeInTheDocument();
  });

  it("canceling the inline pencil rename does not request a mutation", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("button", { name: 'Rename "First chat"' }));
    expect(screen.getByRole("textbox", { name: 'Rename "First chat"' })).toHaveValue("First chat");
    await user.click(screen.getByRole("button", { name: "Cancel rename" }));
    expect(screen.queryByRole("textbox", { name: 'Rename "First chat"' })).toBeNull();
    expect(intents.some((entry) => entry[1] === "renameChat")).toBe(false);
    expect(screen.getByRole("dialog", { name: "Chat library" })).toBeInTheDocument();
  });

  it("successful rename removes its inline editor only after the request resolves truthy", async () => {
    const { user, request } = setup();
    let finish!: (value: unknown) => void;
    request.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("button", { name: 'Rename "First chat"' }));
    const field = screen.getByRole("textbox", { name: 'Rename "First chat"' });
    await user.type(field, " changed");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(field).toBeInTheDocument();
    await act(async () => { finish(true); });
    await waitFor(() => expect(screen.queryByRole("textbox", { name: 'Rename "First chat"' })).toBeNull());
  });

  it("a falsy rename response leaves the editor available for correction", async () => {
    const { user, request } = setup();
    request.mockResolvedValueOnce(null);
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("button", { name: 'Rename "First chat"' }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/couldn't rename/i));
    expect(screen.getByRole("textbox", { name: 'Rename "First chat"' })).toBeInTheDocument();
  });

  it("a rejected rename logs an error and retains the inline editor", async () => {
    const { user, request } = setup();
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    request.mockRejectedValueOnce(new Error("socket dropped"));
    try {
      await user.click(screen.getByText("open library"));
      await user.click(screen.getByRole("button", { name: 'Rename "First chat"' }));
      await user.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(error).toHaveBeenCalledWith("[chat-library] Rename failed:", expect.any(Error)));
      expect(screen.getByRole("textbox", { name: 'Rename "First chat"' })).toBeInTheDocument();
    } finally { error.mockRestore(); }
  });

  it("Delete confirmation names the chosen row and never mutates another row", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    const dialog = await startDelete(user, "Second chat");
    expect(within(dialog).getByText(/Second chat/)).toBeInTheDocument();
    expect(screen.getByText("First chat")).toBeInTheDocument();
    expect(intents.some((entry) => entry[1] === "deleteChat")).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(intents.filter((entry) => entry[1] === "deleteChat"))
      .toEqual([["app-chat-library", "deleteChat", [2]]]));
  });

  it("canceling a row-scoped delete sends no mutation", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    const dialog = await startDelete(user, "Second chat");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(intents.some((entry) => entry[1] === "deleteChat")).toBe(false);
    expect(screen.getByText("Second chat")).toBeInTheDocument();
  });

  it("successful delete closes its confirmation", async () => {
    const { user, request } = setup();
    request.mockResolvedValueOnce(true);
    await user.click(screen.getByText("open library"));
    await startDelete(user, "Second chat");
    await user.click(screen.getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("app-chat-library", "deleteChat", [2]));
    expect(screen.queryByRole("dialog", { name: "Delete chat?" })).toBeNull();
  });

  it("a falsy delete reply retains a retryable confirmation instead of silently dismissing it", async () => {
    const { user, request } = setup();
    request.mockResolvedValueOnce(false);
    await user.click(screen.getByText("open library"));
    await startDelete(user, "Second chat");
    await user.click(screen.getByRole("button", { name: "Delete chat" }));
    const retry = await screen.findByRole("dialog", { name: "Delete chat?" });
    expect(within(retry).getByText(/may have changed elsewhere/i)).toBeInTheDocument();
    expect(request).toHaveBeenCalledTimes(1);
    await user.click(within(retry).getByRole("button", { name: "Cancel" }));
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("a rejected delete logs and offers another explicit confirm without auto-retrying", async () => {
    const { user, request } = setup();
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    request.mockRejectedValueOnce(new Error("connection lost"));
    try {
      await user.click(screen.getByText("open library"));
      await startDelete(user, "Second chat");
      await user.click(screen.getByRole("button", { name: "Delete chat" }));
      const retry = await screen.findByRole("dialog", { name: "Delete chat?" });
      expect(within(retry).getByText(/details were written to the log/i)).toBeInTheDocument();
      expect(error).toHaveBeenCalled();
      expect(request).toHaveBeenCalledTimes(1);
      await user.click(within(retry).getByRole("button", { name: "Cancel" }));
      expect(request).toHaveBeenCalledTimes(1);
    } finally { error.mockRestore(); }
  });

  it("renders the backend DB-read notice even with populated chats", async () => {
    const { user, push } = setup();
    await user.click(screen.getByText("open library"));
    act(() => push({ ...SNAPSHOT, revision: 2, notice: "Could not load saved chats: disk error" }));
    expect(screen.getByRole("status")).toHaveTextContent(/Could not load saved chats: disk error/);
  });

  it("workspace picker shows only selected graphs, and All restores the full list", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open library"));
    await chooseWorkspace(user, "Work");
    expect(screen.getByText("Second chat")).toBeInTheDocument();
    expect(screen.queryByText("First chat")).toBeNull();
    await chooseWorkspace(user, "All workspaces");
    expect(screen.getByText("First chat")).toBeInTheDocument();
    expect(screen.getByText("Second chat")).toBeInTheDocument();
  });

  it("Enter commits a trimmed workspace name in the inline manager", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    await openWorkspaceManager(user);
    await user.click(screen.getByRole("button", { name: "Create workspace" }));
    const field = screen.getByRole("textbox", { name: "New workspace name" });
    await user.type(field, "  Product docs  {Enter}");
    expect(intents).toContainEqual(["app-chat-library", "createWorkspace", ["Product docs"]]);
    expect(screen.queryByRole("textbox", { name: "New workspace name" })).toBeNull();
  });

  it("Escape cancels workspace creation without dispatching", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    await openWorkspaceManager(user);
    await user.click(screen.getByRole("button", { name: "Create workspace" }));
    await user.type(screen.getByRole("textbox", { name: "New workspace name" }), "Discard me{Escape}");
    expect(screen.queryByRole("textbox", { name: "New workspace name" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "Chat library" })).toBeInTheDocument();
    expect(intents.some((entry) => entry[1] === "createWorkspace")).toBe(false);
  });


  it("tag chips require every selected tag to be present (AND filtering)", async () => {
    const { user, push } = setup();
    act(() => push({ ...SNAPSHOT, revision: 2, rows: [
      row({ id: 11, title: "Both tags", tags: ["alpha", "beta"] }),
      row({ id: 12, title: "Alpha only", tags: ["alpha"] }),
      row({ id: 13, title: "Beta only", tags: ["beta"] }),
    ] }));
    await user.click(screen.getByText("open library"));
    await user.click(within(screen.getByRole("group", { name: "Filter by tag" })).getByRole("button", { name: "alpha" }));
    expect(screen.getByText("Alpha only")).toBeInTheDocument();
    expect(screen.queryByText("Beta only")).toBeNull();
    await user.click(within(screen.getByRole("group", { name: "Filter by tag" })).getByRole("button", { name: "beta" }));
    expect(screen.getByText("Both tags")).toBeInTheDocument();
    expect(screen.queryByText("Alpha only")).toBeNull();
    expect(screen.queryByText("Beta only")).toBeNull();
  });

  it("star toggles on an unfavorited row with the chosen id", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("button", { name: 'Add "Second chat" to favorites' }));
    expect(intents).toContainEqual(["app-chat-library", "setGraphFavorite", [2, true]]);
  });

  it("the already-favorited star offers Remove and dispatches false", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("button", { name: 'Remove "First chat" from favorites' }));
    expect(intents).toContainEqual(["app-chat-library", "setGraphFavorite", [1, false]]);
  });

  it("archived chats are excluded on first render without selecting Archived", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open library"));
    expect(screen.getByRole("radio", { name: "All" })).toBeChecked();
    expect(screen.queryByText("Archived chat")).toBeNull();
    expect(screen.getByText("First chat")).toBeInTheDocument();
  });

  it("archiving sends setGraphArchived and hides that row until Archived is selected", async () => {
    const { user, intents, push } = setup();
    await user.click(screen.getByText("open library"));
    await chooseRowAction(user, "Second chat", "Archive");
    expect(intents).toContainEqual(["app-chat-library", "setGraphArchived", [2, true]]);
    act(() => push({ ...SNAPSHOT, revision: 2, rows: SNAPSHOT.rows.map((item) =>
      item.id === 2 ? { ...item, archived: true } : item) }));
    expect(screen.queryByText("Second chat")).toBeNull();
    await user.click(screen.getByRole("radio", { name: "Archived" }));
    expect(screen.getByText("Second chat")).toBeInTheDocument();
    await chooseRowAction(user, "Second chat", "Unarchive");
    expect(intents).toContainEqual(["app-chat-library", "setGraphArchived", [2, false]]);
  });

  it("filter-only no matches uses Clear filters, not the library empty state", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open library"));
    await user.click(screen.getByRole("radio", { name: "Starred" }));
    await user.click(within(screen.getByRole("group", { name: "Filter by tag" }))
      .getByRole("button", { name: "design" }));
    expect(screen.getByText("First chat")).toBeInTheDocument();
    await chooseWorkspace(user, "Work");
    await user.click(screen.getByRole("radio", { name: "Starred" }));
    expect(screen.getByText("No matching chats")).toBeInTheDocument();
    expect(screen.queryByText("No saved chats yet")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByText("First chat")).toBeInTheDocument();
  });

  it("tag editing starts with existing tags and commits trimmed comma-separated tags", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    await chooseRowAction(user, "First chat", "Tag…");
    const field = screen.getByRole("textbox", { name: 'Edit tags for "First chat"' });
    expect(field).toHaveValue("design");
    await user.clear(field);
    await user.type(field, "  alpha , beta, , gamma  {Enter}");
    expect(intents).toContainEqual(["app-chat-library", "setGraphTags", [1, ["alpha", "beta", "gamma"]]]);
  });

  it("Escape cancels tag editing without sending setGraphTags or closing the dialog", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    await chooseRowAction(user, "First chat", "Tag…");
    await user.type(screen.getByRole("textbox", { name: 'Edit tags for "First chat"' }), "{Escape}");
    expect(screen.queryByRole("textbox", { name: 'Edit tags for "First chat"' })).toBeNull();
    expect(intents.some((entry) => entry[1] === "setGraphTags")).toBe(false);
    expect(screen.getByRole("dialog", { name: "Chat library" })).toBeInTheDocument();
  });

  it("only concrete workspaces, never All, have settings controls", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open library"));
    await openWorkspaceManager(user);
    expect(screen.getByRole("button", { name: "Default" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("button", { name: "Work" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "All workspaces" })).toBeNull();
    expect(screen.queryByRole("button", { name: "All" })).toBeNull();
  });

  it("workspace settings open inline with a distinct No default set message", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open library"));
    await openWorkspaceManager(user);
    await user.click(screen.getByRole("button", { name: "Default" }));
    expect(screen.getByText("No default set")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Default model for Default" })).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Chat library" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: /Workspace settings/ })).toBeNull();
  });

  it("preconfigured workspace default displays the provider and friendly model name", async () => {
    const { user, push } = setup();
    act(() => {
      push({ ...SNAPSHOT, revision: 2, workspaces: [
        workspace({ defaultModelProvider: "Ollama (Local)", defaultModelId: "mistral" }),
        workspace({ id: 2, name: "Work" }),
      ] });
      push(composerSnapshot(), "app-composer");
    });
    await user.click(screen.getByText("open library"));
    await openWorkspaceManager(user);
    await user.click(screen.getByRole("button", { name: "Default" }));
    expect(screen.getByText("Current default: Ollama (Local) / Mistral")).toBeInTheDocument();
    expect(screen.queryByText("No default set")).toBeNull();
  });

  it("reclicking workspace settings hides the inline panel", async () => {
    const { user } = setup();
    await user.click(screen.getByText("open library"));
    await openWorkspaceManager(user);
    await user.click(screen.getByRole("button", { name: "Default" }));
    expect(screen.getByRole("button", { name: "Default model for Default" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Default" }));
    expect(screen.queryByRole("button", { name: "Default model for Default" })).toBeNull();
  });

  it("workspace model selection sends the active composer provider and chosen id", async () => {
    const { user, intents, push } = setup();
    act(() => push(composerSnapshot(), "app-composer"));
    await user.click(screen.getByText("open library"));
    await openWorkspaceManager(user);
    await user.click(screen.getByRole("button", { name: "Default" }));
    await user.click(screen.getByRole("button", { name: "Default model for Default" }));
    await user.click(within(await screen.findByRole("listbox", { name: "Default model for Default" }))
      .getByRole("option", { name: "Llama 3" }));
    expect(intents).toContainEqual(["app-chat-library", "setWorkspaceDefaultModel", [1, "Ollama (Local)", "llama3"]]);
  });

  it("No workspace default clears BOTH provider and model id", async () => {
    const { user, intents, push } = setup();
    act(() => {
      push({ ...SNAPSHOT, revision: 2, workspaces: [
        workspace({ defaultModelProvider: "Ollama (Local)", defaultModelId: "llama3" }),
      ] });
      push(composerSnapshot(), "app-composer");
    });
    await user.click(screen.getByText("open library"));
    await openWorkspaceManager(user);
    await user.click(screen.getByRole("button", { name: "Default" }));
    await user.click(screen.getByRole("button", { name: "Default model for Default" }));
    await user.click(within(await screen.findByRole("listbox", { name: "Default model for Default" }))
      .getByRole("option", { name: "No workspace default" }));
    expect(intents).toContainEqual(["app-chat-library", "setWorkspaceDefaultModel", [1, "", ""]]);
  });

  it("a newer workspace snapshot updates an already-open settings panel live", async () => {
    const { user, push } = setup();
    act(() => push(composerSnapshot(), "app-composer"));
    await user.click(screen.getByText("open library"));
    await openWorkspaceManager(user);
    await user.click(screen.getByRole("button", { name: "Default" }));
    expect(screen.getByText("No default set")).toBeInTheDocument();
    act(() => push({ ...SNAPSHOT, revision: 3, workspaces: [
      workspace({ defaultModelProvider: "Ollama (Local)", defaultModelId: "llama3" }),
      workspace({ id: 2, name: "Work" }),
    ] }));
    expect(screen.getByText("Current default: Ollama (Local) / Llama 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Default model for Default" })).toBeInTheDocument();
  });

  it("empty active-provider catalog shows a placeholder but allows clearing a default", async () => {
    const { user, push, intents } = setup();
    const composer = composerSnapshot();
    act(() => {
      push({ ...composer, route: { ...composer.route, modelOptions: [] } }, "app-composer");
      push({ ...SNAPSHOT, revision: 2, workspaces: [
        workspace({ defaultModelProvider: "Ollama (Local)", defaultModelId: "llama3" }),
      ] });
    });
    await user.click(screen.getByText("open library"));
    await openWorkspaceManager(user);
    await user.click(screen.getByRole("button", { name: "Default" }));
    expect(screen.getByText("No models available for Ollama (Local).")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Default model for Default" }));
    const listbox = await screen.findByRole("listbox", { name: "Default model for Default" });
    expect(within(listbox).queryByRole("option", { name: "Llama 3" })).toBeNull();
    await user.click(within(listbox).getByRole("option", { name: "No workspace default" }));
    expect(intents).toContainEqual(["app-chat-library", "setWorkspaceDefaultModel", [1, "", ""]]);
  });

  it("workspace export and import use the selected workspace id and describe the target", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));
    await openWorkspaceManager(user);
    await user.click(screen.getByRole("button", { name: "Work" }));
    expect(screen.getByText("Workspace settings for Work. Export or import this workspace.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Export workspace…" }));
    expect(intents).toContainEqual(["app-chat-library", "exportWorkspace", [2]]);
    await user.click(screen.getByRole("button", { name: "Import workspace…" }));
    await waitFor(() => expect(intents).toContainEqual(["app-chat-library", "importWorkspace", [2]]));
  });

  it("workspace import is transactional and refuses a concurrent second import", async () => {
    const { user, request, intents } = setup();
    let finish!: (value: unknown) => void;
    request.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    await user.click(screen.getByText("open library"));
    await openWorkspaceManager(user);
    await user.click(screen.getByRole("button", { name: "Default" }));
    await user.click(screen.getByRole("button", { name: "Import workspace…" }));
    expect(intents.filter((item) => item[1] === "importWorkspace")).toHaveLength(1);
    const button = screen.getByRole("button", { name: "Import workspace…" });
    expect(button).toBeDisabled();
    await act(async () => { finish({ imported: true }); });
    await waitFor(() => expect(button).toBeEnabled());
  });

});
