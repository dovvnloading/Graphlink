import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialChatLibraryState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

const ROWS = [
  { id: 1, title: "First chat", createdLabel: "Jul 01, 2026 09:30 AM", updatedLabel: "Jul 05, 2026 02:00 PM" },
  { id: 2, title: "Second chat", createdLabel: "Jul 02, 2026 10:00 AM", updatedLabel: "Jul 06, 2026 11:15 AM" },
];

describe("App against the mock bridge", () => {
  afterEach(cleanup);

  it("renders the empty placeholder and the default toolbar", () => {
    render(<App />);

    expect(screen.getByText("No saved chats yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New Chat" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rename" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  });
});

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    refresh: vi.fn(),
    loadChat: vi.fn(),
    deleteChat: vi.fn(),
    renameChat: vi.fn(),
    newChat: vi.fn(),
    close: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { chatLibraryBridge: remote } });
    }
  }
  const qtWindow = window as unknown as QtWindow;
  qtWindow.QWebChannel = FakeQWebChannel as unknown as QtWindow["QWebChannel"];
  qtWindow.qt = { webChannelTransport: {} };
  return remote;
}

function uninstall() {
  const qtWindow = window as unknown as QtWindow;
  delete qtWindow.QWebChannel;
  delete qtWindow.qt;
}

type Remote = ReturnType<typeof installFakeQWebChannel>;

function push(remote: Remote, rows = ROWS, extra: Record<string, unknown> = {}) {
  const handler = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
  handler(JSON.stringify({ ...initialChatLibraryState, revision: 1, rows, ...extra }));
}

describe("App against a real (faked) QWebChannel connection", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("renders each saved chat row with its title and formatted timestamps", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote);

    expect(await screen.findByText("First chat")).toBeInTheDocument();
    expect(screen.getByText("Second chat")).toBeInTheDocument();
    expect(screen.getByText(/Updated Jul 05, 2026 02:00 PM/)).toBeInTheDocument();
    expect(screen.getByText("2 saved chats.")).toBeInTheDocument();
  });

  it("filters the list client-side as the user types, with no bridge round-trip", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByText("First chat");

    await user.type(screen.getByRole("textbox", { name: "Search chats" }), "second");

    expect(screen.queryByText("First chat")).not.toBeInTheDocument();
    expect(screen.getByText("Second chat")).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 2 saved chats.")).toBeInTheDocument();
    expect(remote.refresh).not.toHaveBeenCalled();
  });

  it("double-clicking a row loads it through the bridge", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);

    await user.dblClick(await screen.findByText("Second chat"));

    expect(remote.loadChat).toHaveBeenCalledWith(2);
  });

  it("Enter in the search box loads the selected row", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByText("First chat");

    const search = screen.getByRole("textbox", { name: "Search chats" });
    await user.click(search);
    await user.keyboard("{ArrowDown}{Enter}"); // move to Second chat, then load

    expect(remote.loadChat).toHaveBeenCalledWith(2);
  });

  it("delete is a two-step confirm: first click arms, Confirm Delete commits", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await user.click(await screen.findByText("Second chat"));

    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.getByText(/Delete this chat\? This cannot be undone\./)).toBeInTheDocument();
    expect(remote.deleteChat).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Confirm Delete" }));
    expect(remote.deleteChat).toHaveBeenCalledWith(2);
  });

  it("delete confirm can be cancelled without deleting", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await user.click(await screen.findByText("First chat"));

    await user.click(screen.getByRole("button", { name: "Delete" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(remote.deleteChat).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "New Chat" })).toBeInTheDocument();
  });

  it("rename opens an inline input prefilled with the title and commits the trimmed value", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await user.click(await screen.findByText("First chat"));

    await user.click(screen.getByRole("button", { name: "Rename" }));
    const input = screen.getByRole("textbox", { name: "New chat title" });
    expect(input).toHaveValue("First chat");

    await user.clear(input);
    await user.type(input, "  Renamed  ");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(remote.renameChat).toHaveBeenCalledWith(1, "Renamed");
  });

  it("rename Save is disabled for an empty draft", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await user.click(await screen.findByText("First chat"));

    await user.click(screen.getByRole("button", { name: "Rename" }));
    await user.clear(screen.getByRole("textbox", { name: "New chat title" }));

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(remote.renameChat).not.toHaveBeenCalled();
  });

  it("New Chat calls the bridge (its native confirm lives Python-side)", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByText("First chat");

    await user.click(screen.getByRole("button", { name: "New Chat" }));

    expect(remote.newChat).toHaveBeenCalledTimes(1);
  });

  it("Escape in the search box closes the dialog through the bridge", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByText("First chat");

    await user.click(screen.getByRole("textbox", { name: "Search chats" }));
    await user.keyboard("{Escape}");

    expect(remote.close).toHaveBeenCalledTimes(1);
  });

  it("Escape cancels an armed delete confirm before it would close the dialog", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await user.click(await screen.findByText("First chat"));
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await user.click(screen.getByRole("textbox", { name: "Search chats" }));
    await user.keyboard("{Escape}");

    expect(remote.close).not.toHaveBeenCalled();
    expect(screen.queryByText(/Delete this chat\?/)).not.toBeInTheDocument();
  });

  it("a recoverable notice from Python renders inline above the list", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote, ROWS, { notice: "Failed to load chat: corrupt row" });

    expect(await screen.findByText("Failed to load chat: corrupt row")).toBeInTheDocument();
    // The list is still usable - not replaced by a full-surface error.
    expect(screen.getByText("First chat")).toBeInTheDocument();
  });

  it("renders the shared full-surface error state only on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const handler = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    handler(JSON.stringify({ ...initialChatLibraryState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Chat library unavailable")).toBeInTheDocument();
  });

  it("keeps the selected row highlighted via aria-selected", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);

    const secondRow = (await screen.findByText("Second chat")).closest("li")!;
    await user.click(within(secondRow).getByText("Second chat"));

    expect(secondRow).toHaveAttribute("aria-selected", "true");
  });
});
