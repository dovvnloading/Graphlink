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
    // ADR-020 stage 20.2.
    workspaceId: 1,
    favorite: false,
    archived: false,
    tags: [] as string[],
    ...overrides,
  };
}

// ADR-020 stage 20.3: defaultModelProvider/defaultModelId default to "" -
// matching AppWorkspaceRowPayload's own "empty string on both = no
// workspace default set" wire contract, not an omitted/optional field.
function workspace(overrides: Record<string, unknown>) {
  return { id: 1, name: "Default", icon: "", archived: false, defaultModelProvider: "", defaultModelId: "", ...overrides };
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
  workspaces: [workspace({})],
  notice: null,
};

// ADR-020 stage 20.3: a minimal but real AppComposerState snapshot - the
// workspace default-model picker reads route.modelOptions/route.provider
// from this same "app-composer" topic Composer.tsx itself reads, per this
// file's own module docstring.
function composerSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    schemaVersion: 1,
    minCompatibleSchemaVersion: 1,
    revision: 1,
    draft: { id: "", text: "", contextMode: "branch", sendMode: "enter_to_send", restored: false },
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

// ADR-020 stage 20.3: keyed by topic (not a single shared listener) - this
// dialog now holds two independent subscriptions ("app-chat-library" and
// "app-composer", see the module docstring on ChatLibraryDialog.tsx), so a
// single-listener fake would have the SECOND subscribe() call silently
// clobber the first, breaking every pre-existing push() call in this file.
// push() defaults to "app-chat-library" so those calls stay unchanged.
function makeTransport() {
  const intents: unknown[][] = [];
  const resubscribes: string[] = [];
  const listeners = new Map<string, (payload: Record<string, unknown>) => void>();
  // REVIEW-FIX: confirmDelete now goes through transport.request() instead
  // of fireIntent, the same "I need the actual reply" shape as
  // DiagnosticsDialog.test.tsx's own makeTransport() (see that file's own
  // comment) - a vi.fn() so individual tests can drive its resolution/
  // rejection, wired to also record into the same `intents` array so one
  // assertion list still covers all three call shapes.
  // Defaults to a successful delete so every test that doesn't care about
  // the specific resolved value (most of them) doesn't also have to arrange
  // one - tests exercising the falsy/rejected paths override this per-call
  // with mockResolvedValueOnce/mockRejectedValueOnce.
  const request = vi.fn<(topic: string, intent: string, args?: unknown[]) => Promise<unknown>>()
    .mockResolvedValue(true);
  const transport = {
    subscribe: (topic: string, l: (payload: Record<string, unknown>) => void) => {
      listeners.set(topic, l);
      return () => {
        listeners.delete(topic);
      };
    },
    // ADR-020 stage 20.5: ChatLibraryDialog.tsx now calls this unconditionally
    // right after subscribe() - see that file's own comment for why (a
    // second, independent "app-chat-library" subscriber, QuickSwitcherDialog.
    // tsx, can otherwise steal the one-time fresh-snapshot push).
    resubscribe: (topic: string) => {
      resubscribes.push(topic);
    },
    intent: (topic: string, intent: string, args: unknown[]) => {
      intents.push([topic, intent, args]);
    },
    // ADR-003 stage 3.1: ChatLibraryDialog's own mutating call sites now go
    // through fireIntent, not the bare intent() above.
    fireIntent: (topic: string, intent: string, args: unknown[] = []) => {
      intents.push([topic, intent, args]);
    },
    request: (topic: string, intent: string, args: unknown[] = []) => {
      intents.push([topic, intent, args]);
      return request(topic, intent, args);
    },
  } as unknown as WsTransport;
  return {
    transport,
    intents,
    resubscribes,
    request,
    push: (payload: Record<string, unknown>, topic: string = "app-chat-library") => listeners.get(topic)?.(payload),
  };
}

// ADR-020 stage 20.3: identical helper to SettingsDialog.test.tsx's own -
// CustomSelect's option panel portals to document.body asynchronously
// after the trigger click, so the option lookup must be findBy, not getBy.
async function chooseCustomOption(
  user: ReturnType<typeof userEvent.setup>,
  triggerName: string,
  optionName: string,
) {
  await user.click(screen.getByRole("button", { name: triggerName }));
  await user.click(await screen.findByRole("button", { name: optionName }));
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

  it("New Chat dispatches the newChat intent with no workspaceId while 'All' is selected, and closes the dialog", async () => {
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

  it("Escape while renaming cancels the rename WITHOUT closing the dialog (R8a finding #16)", async () => {
    // This test used to assert the opposite - Escape closing the whole
    // dialog mid-rename, mislabeled as "the app's global Escape-to-close
    // policy" - which was exactly the bug the UI/UX issue list's finding
    // #16 named this component as its own worked example of: the rename
    // input's own onKeyDown already called event.preventDefault() on
    // Escape (see onRenameKeyDown below), but overlays.tsx's document-level
    // handler ran in capture phase with an unconditional stopPropagation(),
    // so that preventDefault() could never matter - the global handler
    // always won and closed the dialog out from under an in-progress
    // rename. overlays.tsx now runs on bubble phase and checks
    // event.defaultPrevented before closing anything, so this component's
    // own already-correct handler finally has a chance to run.
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Rename "First Chat"'));
    await user.keyboard("{Escape}");

    expect(intents.filter((i) => i[1] === "renameChat")).toEqual([]);
    // Reverts exactly like the Cancel (x) button does, not "the dialog is
    // now gone" - the rename INPUT is gone (cancelled) and the original
    // title is back, but Chat Library stays open underneath it. Checked by
    // role, not just the accessible name: the pencil button that RESTARTS
    // a rename carries the identical "Rename ..." label, so a label-only
    // query would find that instead and pass even if cancelling had failed.
    expect(screen.queryByRole("textbox", { name: 'Rename "First Chat"' })).toBeNull();
    expect(screen.getByText("First Chat")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  // -- REVIEW-FIX: commitRename used to fire-and-forget via fireIntent and
  // -- clear renamingId the instant the message was SENT, not once the
  // -- rename was actually confirmed - the same fix confirmDelete already
  // -- got below, applied here since renameChat's own intent now returns a
  // -- real success/failure signal (null when the row is gone) instead of
  // -- always resolving to undefined.

  it("a confirmed rename that actually applies closes the rename input", async () => {
    const { user, request } = setup();
    await user.click(screen.getByText("open library"));
    request.mockResolvedValueOnce("2026-01-16 09:00:00.000000");

    await user.click(screen.getByLabelText('Rename "First Chat"'));
    const input = screen.getByLabelText('Rename "First Chat"') as HTMLInputElement;
    await user.clear(input);
    await user.type(input, "Renamed Title");
    await user.click(screen.getByLabelText('Save "First Chat"'));

    // Back to the normal (non-renaming) row action - the pencil button, not
    // the input, now carries this label.
    await screen.findByRole("button", { name: 'Rename "First Chat"' });
    expect(screen.queryByRole("textbox", { name: 'Rename "First Chat"' })).toBeNull();
  });

  it("a resolved-but-falsy rename (the row was already gone) leaves the rename input in place rather than optimistically closing it", async () => {
    const { user, request } = setup();
    await user.click(screen.getByText("open library"));
    request.mockResolvedValueOnce(null);

    await user.click(screen.getByLabelText('Rename "First Chat"'));
    const input = screen.getByLabelText('Rename "First Chat"') as HTMLInputElement;
    await user.clear(input);
    await user.type(input, "Renamed Title");
    await user.click(screen.getByLabelText('Save "First Chat"'));
    await vi.waitFor(() => expect(request).toHaveBeenCalled());

    expect(screen.getByRole("textbox", { name: 'Rename "First Chat"' })).toBeInTheDocument();
  });

  it("a rejected rename request leaves the rename input in place and logs, without throwing", async () => {
    const { user, request } = setup();
    await user.click(screen.getByText("open library"));
    request.mockRejectedValueOnce(new Error("socket dropped"));
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    await user.click(screen.getByLabelText('Rename "First Chat"'));
    const input = screen.getByLabelText('Rename "First Chat"') as HTMLInputElement;
    await user.clear(input);
    await user.type(input, "Renamed Title");
    await user.click(screen.getByLabelText('Save "First Chat"'));
    await vi.waitFor(() => expect(consoleError).toHaveBeenCalled());

    expect(screen.getByRole("textbox", { name: 'Rename "First Chat"' })).toBeInTheDocument();
    consoleError.mockRestore();
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

  // -- REVIEW-FIX: confirmDelete used to fire-and-forget via fireIntent and
  // -- clear its confirm state the instant the message was SENT, not once
  // -- the deletion was actually confirmed - a genuine failure left the row
  // -- sitting in the list with its "Delete?" buttons already reverted to
  // -- normal, no visible sign anything had gone wrong.

  it("a confirmed delete that actually removes the row closes the confirm UI", async () => {
    const { user, request } = setup();
    await user.click(screen.getByText("open library"));
    request.mockResolvedValueOnce(true);

    await user.click(screen.getByLabelText('Delete "First Chat"'));
    await user.click(screen.getByLabelText('Confirm delete "First Chat"'));

    await screen.findByLabelText('Delete "First Chat"'); // back to the normal (non-confirming) row action
    expect(screen.queryByText("Delete?")).toBeNull();
  });

  it("a resolved-but-falsy delete (the row was already gone) leaves the confirm UI in place rather than optimistically closing it", async () => {
    const { user, request } = setup();
    await user.click(screen.getByText("open library"));
    request.mockResolvedValueOnce(false);

    await user.click(screen.getByLabelText('Delete "First Chat"'));
    await user.click(screen.getByLabelText('Confirm delete "First Chat"'));
    await vi.waitFor(() => expect(request).toHaveBeenCalled());

    expect(screen.getByText("Delete?")).toBeInTheDocument();
  });

  it("a rejected delete request leaves the confirm UI in place and logs, without throwing", async () => {
    const { user, request } = setup();
    await user.click(screen.getByText("open library"));
    request.mockRejectedValueOnce(new Error("socket dropped"));
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    await user.click(screen.getByLabelText('Delete "First Chat"'));
    await user.click(screen.getByLabelText('Confirm delete "First Chat"'));
    await vi.waitFor(() => expect(consoleError).toHaveBeenCalled());

    expect(screen.getByText("Delete?")).toBeInTheDocument();
    consoleError.mockRestore();
  });

  it("a DB-read notice still renders when present", async () => {
    const { user, push } = setup();
    act(() => push({ ...snapshot, notice: "Could not load saved chats: disk error" }));
    await user.click(screen.getByText("open library"));

    expect(screen.getByText("Could not load saved chats: disk error")).toBeInTheDocument();
  });

  // -- ADR-020 stage 20.2: workspace switcher -------------------------------

  it("workspace tabs: switching to a specific workspace filters the visible list to that workspace's graphs, 'All' shows every workspace", async () => {
    const { user, push } = setup();
    act(() =>
      push({
        ...snapshot,
        workspaces: [workspace({ id: 1, name: "Default" }), workspace({ id: 2, name: "Work" })],
        rows: [
          row({ id: 1, title: "First Chat", workspaceId: 1 }),
          row({ id: 2, title: "Second Chat", workspaceId: 2 }),
        ],
      }),
    );
    await user.click(screen.getByText("open library"));

    // "All" is the default selection - both workspaces' graphs are visible.
    expect(screen.getByText("First Chat")).toBeInTheDocument();
    expect(screen.getByText("Second Chat")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Work" }));
    expect(screen.queryByText("First Chat")).toBeNull();
    expect(screen.getByText("Second Chat")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "All" }));
    expect(screen.getByText("First Chat")).toBeInTheDocument();
    expect(screen.getByText("Second Chat")).toBeInTheDocument();
  });

  it("New Chat passes the selected workspace's id once a specific workspace tab (not All) is active", async () => {
    const { user, push, intents } = setup();
    act(() => push({ ...snapshot, workspaces: [workspace({ id: 1, name: "Default" }), workspace({ id: 2, name: "Work" })] }));
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByRole("button", { name: "Work" }));
    await user.click(screen.getByRole("button", { name: "New Chat" }));

    expect(intents).toContainEqual(["app-chat-library", "newChat", [2]]);
  });

  it("the + Workspace affordance reveals an inline input; Enter commits createWorkspace with the trimmed name", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByRole("button", { name: "+ Workspace" }));
    const input = screen.getByLabelText("New workspace name");
    await user.type(input, "  Research  {Enter}");

    expect(intents).toContainEqual(["app-chat-library", "createWorkspace", ["Research"]]);
  });

  it("Escape while creating a workspace cancels without dispatching createWorkspace", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByRole("button", { name: "+ Workspace" }));
    await user.keyboard("{Escape}");

    expect(intents.filter((i) => i[1] === "createWorkspace")).toEqual([]);
    expect(screen.queryByLabelText("New workspace name")).toBeNull();
  });

  // -- ADR-020 stage 20.2: tag filter chips (AND semantics) -----------------

  it("tag filter chips use AND semantics: a graph must carry every selected tag to remain visible", async () => {
    const { user, push } = setup();
    act(() =>
      push({
        ...snapshot,
        rows: [
          row({ id: 1, title: "Alpha", tags: ["work", "urgent"] }),
          row({ id: 2, title: "Beta", tags: ["work"] }),
          row({ id: 3, title: "Gamma", tags: ["urgent"] }),
        ],
      }),
    );
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByRole("button", { name: "work" }));
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.queryByText("Gamma")).toBeNull();

    // Adding a second chip narrows further (AND, not OR) - only the row
    // carrying BOTH selected tags remains.
    await user.click(screen.getByRole("button", { name: "urgent" }));
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.queryByText("Beta")).toBeNull();
    expect(screen.queryByText("Gamma")).toBeNull();
  });

  // -- ADR-020 stage 20.2: favorite / archive icon buttons ------------------

  it("the star icon button fires setGraphFavorite with the toggled value", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Add "First Chat" to favorites'));
    expect(intents).toContainEqual(["app-chat-library", "setGraphFavorite", [1, true]]);
  });

  it("an already-favorited row's star button reads 'remove from favorites' and fires favorite:false", async () => {
    const { user, push, intents } = setup();
    act(() => push({ ...snapshot, rows: [row({ id: 1, title: "First Chat", favorite: true })] }));
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Remove "First Chat" from favorites'));
    expect(intents).toContainEqual(["app-chat-library", "setGraphFavorite", [1, false]]);
  });

  it("the archive icon button fires setGraphArchived, and archived rows are hidden by default until the Archived toggle is switched on", async () => {
    const { user, push, intents } = setup();
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Archive "First Chat"'));
    expect(intents).toContainEqual(["app-chat-library", "setGraphArchived", [1, true]]);

    // Simulate the backend republishing with the row now archived.
    act(() => push({ ...snapshot, rows: [row({ id: 1, title: "First Chat", archived: true }), snapshot.rows[1]] }));
    expect(screen.queryByText("First Chat")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Archived" }));
    expect(screen.getByText("First Chat")).toBeInTheDocument();
  });

  it("the Archived toggle defaults off: archived rows are excluded from the list on first render", async () => {
    const { user, push } = setup();
    act(() =>
      push({
        ...snapshot,
        rows: [row({ id: 1, title: "First Chat", archived: true }), row({ id: 2, title: "Second Chat", archived: false })],
      }),
    );
    await user.click(screen.getByText("open library"));

    expect(screen.queryByText("First Chat")).toBeNull();
    expect(screen.getByText("Second Chat")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Archived" }));
    expect(screen.getByText("First Chat")).toBeInTheDocument();
  });

  it("shows a distinct 'no matches' state for filters (not search), with a Clear filters action", async () => {
    const { user, push } = setup();
    act(() =>
      push({
        ...snapshot,
        rows: [row({ id: 1, title: "First Chat", archived: true }), row({ id: 2, title: "Second Chat", archived: true })],
      }),
    );
    await user.click(screen.getByText("open library"));

    expect(screen.getByText("No chats match the current filters.")).toBeInTheDocument();

    await user.click(screen.getByText("Clear filters"));
    expect(screen.getByText("First Chat")).toBeInTheDocument();
  });

  // -- ADR-020 stage 20.2: inline tag editing --------------------------------

  it("tag editing: the tag icon opens an inline input pre-filled with the row's tags; Enter commits the trimmed/split list via setGraphTags", async () => {
    const { user, push, intents } = setup();
    act(() => push({ ...snapshot, rows: [row({ id: 1, title: "First Chat", tags: ["work", "urgent"] }), snapshot.rows[1]] }));
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Edit tags for "First Chat"'));
    const input = screen.getByLabelText('Edit tags for "First Chat"') as HTMLInputElement;
    expect(input.value).toBe("work, urgent");

    await user.clear(input);
    await user.type(input, "one, two{Enter}");

    expect(intents).toContainEqual(["app-chat-library", "setGraphTags", [1, ["one", "two"]]]);
  });

  it("tag editing: Escape cancels without dispatching setGraphTags", async () => {
    const { user, intents } = setup();
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Edit tags for "First Chat"'));
    await user.keyboard("{Escape}");

    expect(intents.filter((i) => i[1] === "setGraphTags")).toEqual([]);
    expect(screen.getByText("First Chat")).toBeInTheDocument();
  });

  // -- ADR-020 stage 20.3: workspace default-model settings -----------------

  it("the gear settings icon is rendered only for real workspaces, not the 'All' tab", async () => {
    const { user, push } = setup();
    act(() => push({ ...snapshot, workspaces: [workspace({ id: 1, name: "Default" }), workspace({ id: 2, name: "Work" })] }));
    await user.click(screen.getByText("open library"));

    expect(screen.getByLabelText('Show default model settings for "Default"')).toBeInTheDocument();
    expect(screen.getByLabelText('Show default model settings for "Work"')).toBeInTheDocument();
    expect(screen.queryByLabelText('Show default model settings for "All"')).toBeNull();
  });

  it("the gear icon reveals an inline panel (no new modal) showing 'no default set' when unset", async () => {
    const { user, push } = setup();
    act(() => push(composerSnapshot(), "app-composer"));
    await user.click(screen.getByText("open library"));

    expect(screen.queryByText(/Default model for/)).toBeNull();

    await user.click(screen.getByLabelText('Show default model settings for "Default"'));

    // Still the same dialog - no second <Dialog> mounted.
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(screen.getByText('No default set - dispatch falls through to any node/branch pin, then the auto policy.')).toBeInTheDocument();
  });

  it("a workspace with a default already set shows its current provider/model instead of the unset message", async () => {
    const { user, push } = setup();
    act(() => push(composerSnapshot(), "app-composer"));
    act(() =>
      push({
        ...snapshot,
        workspaces: [workspace({ id: 1, name: "Default", defaultModelProvider: "Ollama (Local)", defaultModelId: "llama3" })],
      }),
    );
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Show default model settings for "Default"'));

    expect(screen.getByText("Currently: Ollama (Local) / llama3")).toBeInTheDocument();
  });

  it("clicking the gear icon again hides the panel", async () => {
    const { user, push } = setup();
    act(() => push(composerSnapshot(), "app-composer"));
    await user.click(screen.getByText("open library"));

    const gear = screen.getByLabelText('Show default model settings for "Default"');
    await user.click(gear);
    expect(screen.getByText(/Default model for/)).toBeInTheDocument();

    await user.click(screen.getByLabelText('Hide default model settings for "Default"'));
    expect(screen.queryByText(/Default model for/)).toBeNull();
  });

  it("choosing a model option fires setWorkspaceDefaultModel with the workspace id, the composer's active provider, and the chosen model id", async () => {
    const { user, push, intents } = setup();
    act(() => push(composerSnapshot(), "app-composer"));
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Show default model settings for "Default"'));
    await chooseCustomOption(user, 'Default model for "Default"', "Mistral");

    expect(intents).toContainEqual(["app-chat-library", "setWorkspaceDefaultModel", [1, "Ollama (Local)", "mistral"]]);
  });

  it("choosing 'No workspace default' fires setWorkspaceDefaultModel with empty provider and model id (clearing)", async () => {
    const { user, push, intents } = setup();
    act(() => push(composerSnapshot(), "app-composer"));
    act(() =>
      push({
        ...snapshot,
        workspaces: [workspace({ id: 1, name: "Default", defaultModelProvider: "Ollama (Local)", defaultModelId: "llama3" })],
      }),
    );
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Show default model settings for "Default"'));
    await chooseCustomOption(user, 'Default model for "Default"', "No workspace default");

    expect(intents).toContainEqual(["app-chat-library", "setWorkspaceDefaultModel", [1, "", ""]]);
  });

  it("a republish with the new value (live re-subscribe) updates the panel without closing/reopening the dialog", async () => {
    const { user, push } = setup();
    act(() => push(composerSnapshot(), "app-composer"));
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Show default model settings for "Default"'));
    expect(screen.getByText('No default set - dispatch falls through to any node/branch pin, then the auto policy.')).toBeInTheDocument();

    // Simulate the backend republishing after setWorkspaceDefaultModel commits.
    act(() =>
      push({
        ...snapshot,
        revision: 2,
        workspaces: [workspace({ id: 1, name: "Default", defaultModelProvider: "Ollama (Local)", defaultModelId: "mistral" })],
      }),
    );

    expect(screen.getByText("Currently: Ollama (Local) / mistral")).toBeInTheDocument();
  });

  it("when the active provider's catalog has no models, a placeholder message shows in place of options (clearing still works)", async () => {
    const { user, push, intents } = setup();
    act(() => push(composerSnapshot({ route: { ...composerSnapshot().route, modelOptions: [] } }), "app-composer"));
    act(() =>
      push({
        ...snapshot,
        workspaces: [workspace({ id: 1, name: "Default", defaultModelProvider: "Ollama (Local)", defaultModelId: "llama3" })],
      }),
    );
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Show default model settings for "Default"'));

    expect(screen.getByText(/No models found for Ollama \(Local\)\. Run a scan on its Settings page\./)).toBeInTheDocument();

    await chooseCustomOption(user, 'Default model for "Default"', "No workspace default");
    expect(intents).toContainEqual(["app-chat-library", "setWorkspaceDefaultModel", [1, "", ""]]);
  });

  // -- ADR-020 stage 20.5: workspace export ----------------------------------

  it("the gear panel's Export Workspace button fires exportWorkspace with the workspace id", async () => {
    const { user, push, intents } = setup();
    act(() => push(composerSnapshot(), "app-composer"));
    act(() => push({ ...snapshot, workspaces: [workspace({ id: 1, name: "Default" }), workspace({ id: 2, name: "Work" })] }));
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Show default model settings for "Work"'));
    await user.click(screen.getByRole("button", { name: "Export Workspace…" }));

    expect(intents).toContainEqual(["app-chat-library", "exportWorkspace", [2]]);
  });

  it("names the target workspace in the export panel's own description", async () => {
    const { user, push } = setup();
    act(() => push(composerSnapshot(), "app-composer"));
    act(() => push({ ...snapshot, workspaces: [workspace({ id: 1, name: "Client Alpha" })] }));
    await user.click(screen.getByText("open library"));

    await user.click(screen.getByLabelText('Show default model settings for "Client Alpha"'));

    expect(screen.getByText(/Save every graph in "Client Alpha" as one \.graphlink file\./)).toBeInTheDocument();
  });

  it("regression: resubscribes to app-chat-library on mount, so a late subscribe still gets a fresh snapshot", () => {
    // ADR-020 stage 20.5: in the real app this dialog is lazy-mounted
    // (App.tsx's own LazySurface), so it is no longer guaranteed to be the
    // FIRST subscriber to "app-chat-library" - QuickSwitcherDialog.tsx now
    // also subscribes, eagerly, from app start. WsTransport.subscribe()
    // only sends a real wire-level "subscribe" (and gets a fresh snapshot
    // back) for a topic's first-ever listener - without this dialog's own
    // resubscribe() call on mount, opening Library after the quick switcher
    // had already claimed that slot left it stuck on empty initialState
    // ("No saved chats yet" with a real, populated library) - confirmed via
    // a live browser repro before this fix, not a hypothetical.
    const { resubscribes } = setup();

    expect(resubscribes).toContain("app-chat-library");
  });
});
