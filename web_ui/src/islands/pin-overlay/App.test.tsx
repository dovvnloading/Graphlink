import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialPinOverlayState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

const ROWS = [
  { id: "p1", title: "First pin", note: "a note" },
  { id: "p2", title: "Second pin", note: "" },
];

describe("App against the mock bridge", () => {
  afterEach(cleanup);

  it("renders the empty placeholder and a disabled-nothing Add button", () => {
    render(<App />);

    expect(screen.getByText("No saved locations yet.")).toBeInTheDocument();
    expect(screen.getByText("No saved locations")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add pin here" })).toBeEnabled();
  });
});

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    selectPin: vi.fn(),
    deletePin: vi.fn(),
    createPin: vi.fn(),
    editPin: vi.fn(),
    commitDraft: vi.fn(),
    discardDraft: vi.fn(),
    resize: vi.fn(),
    close: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { pinOverlayBridge: remote } });
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
  handler(JSON.stringify({ ...initialPinOverlayState, revision: 1, rows, ...extra }));
}

describe("App against a real (faked) QWebChannel connection", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("renders each saved pin's title and note", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote);

    expect(await screen.findByText("First pin")).toBeInTheDocument();
    expect(screen.getByText("a note")).toBeInTheDocument();
    expect(screen.getByText("Second pin")).toBeInTheDocument();
    expect(screen.getByText("2 saved locations")).toBeInTheDocument();
  });

  it("filters the list client-side as the user types, with no bridge round-trip", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByText("First pin");

    await user.type(screen.getByRole("textbox", { name: "Search navigation pins" }), "second");

    expect(screen.queryByText("First pin")).not.toBeInTheDocument();
    expect(screen.getByText("Second pin")).toBeInTheDocument();
    expect(screen.getByText("Showing 1 of 2 saved locations")).toBeInTheDocument();
  });

  it("clicking a row's title calls selectPin", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByText("First pin");

    await user.click(screen.getByText("First pin"));

    expect(remote.selectPin).toHaveBeenCalledWith("p1");
  });

  it("the selected pin's row is marked aria-selected", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote, ROWS, { selectedPinId: "p2" });

    const rows = await screen.findAllByRole("option");
    const secondRow = rows.find((row) => row.textContent?.includes("Second pin"));
    expect(secondRow).toHaveAttribute("aria-selected", "true");
  });

  it("Edit and Delete call through without any confirmation, matching legacy", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByText("First pin");

    await user.click(screen.getByRole("button", { name: "Edit First pin" }));
    await user.click(screen.getByRole("button", { name: "Delete First pin" }));

    expect(remote.editPin).toHaveBeenCalledWith("p1");
    expect(remote.deletePin).toHaveBeenCalledWith("p1");
  });

  it("Add pin here calls createPin", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByText("First pin");

    await user.click(screen.getByRole("button", { name: "Add pin here" }));

    expect(remote.createPin).toHaveBeenCalledTimes(1);
  });

  it("Close button calls close", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByText("First pin");

    await user.click(screen.getByRole("button", { name: "Close" }));

    expect(remote.close).toHaveBeenCalledTimes(1);
  });

  it("Escape in the search box calls close", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByText("First pin");

    await user.click(screen.getByRole("textbox", { name: "Search navigation pins" }));
    await user.keyboard("{Escape}");

    expect(remote.close).toHaveBeenCalledTimes(1);
  });

  it("renders the shared error state on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const handler = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    handler(JSON.stringify({ ...initialPinOverlayState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Navigation pins are unavailable")).toBeInTheDocument();
  });
});

describe("App's draft editor view (Phase 5 increment 2)", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  function pushDraft(remote: Remote, draft: Record<string, unknown> | null, extra: Record<string, unknown> = {}) {
    push(remote, ROWS, { draft, ...extra });
  }

  it("replaces the list with the editor view, prefilled, when a new-pin draft begins", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    pushDraft(remote, { pinId: "new-1", title: "Waypoint 3", note: "", isNew: true });

    expect(await screen.findByText("Add navigation pin")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Navigation pin title" })).toHaveValue("Waypoint 3");
    expect(screen.queryByText("First pin")).not.toBeInTheDocument();
  });

  it("shows Edit copy and the existing values when editing an existing pin", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    pushDraft(remote, { pinId: "p1", title: "First pin", note: "a note", isNew: false });

    expect(await screen.findByText("Edit navigation pin")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Navigation pin title" })).toHaveValue("First pin");
    expect(screen.getByRole("textbox", { name: "Navigation pin note" })).toHaveValue("a note");
  });

  it("Save calls commitDraft with the (possibly edited) trimmed values", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    pushDraft(remote, { pinId: "new-1", title: "Waypoint 3", note: "", isNew: true });
    const titleInput = await screen.findByRole("textbox", { name: "Navigation pin title" });

    await user.clear(titleInput);
    await user.type(titleInput, "  Named  ");
    await user.type(screen.getByRole("textbox", { name: "Navigation pin note" }), "  a note  ");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(remote.commitDraft).toHaveBeenCalledWith("Named", "a note");
  });

  it("Save is disabled and shows an inline error for an empty title, without calling commitDraft", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    pushDraft(remote, { pinId: "new-1", title: "Waypoint 3", note: "", isNew: true });
    const titleInput = await screen.findByRole("textbox", { name: "Navigation pin title" });

    await user.clear(titleInput);

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(remote.commitDraft).not.toHaveBeenCalled();
  });

  it("Cancel calls discardDraft", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    pushDraft(remote, { pinId: "new-1", title: "Waypoint 3", note: "", isNew: true });
    await screen.findByText("Add navigation pin");

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(remote.discardDraft).toHaveBeenCalledTimes(1);
  });

  it("Escape in the title field calls discardDraft, not close", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    pushDraft(remote, { pinId: "new-1", title: "Waypoint 3", note: "", isNew: true });
    const titleInput = await screen.findByRole("textbox", { name: "Navigation pin title" });

    await user.click(titleInput);
    await user.keyboard("{Escape}");

    expect(remote.discardDraft).toHaveBeenCalledTimes(1);
    expect(remote.close).not.toHaveBeenCalled();
  });

  it("Enter in the title field commits the draft", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    pushDraft(remote, { pinId: "new-1", title: "Waypoint 3", note: "", isNew: true });
    const titleInput = await screen.findByRole("textbox", { name: "Navigation pin title" });

    await user.click(titleInput);
    await user.keyboard("{Enter}");

    expect(remote.commitDraft).toHaveBeenCalledWith("Waypoint 3", "");
  });

  it("a server-side error (state.error) renders inline when there is no local validation error", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    pushDraft(remote, { pinId: "new-1", title: "Waypoint 3", note: "", isNew: true }, { error: "A title is required" });

    expect(await screen.findByRole("alert")).toHaveTextContent("A title is required");
  });

  it("resets the editable fields when a NEW draft begins after a previous one closed", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    pushDraft(remote, { pinId: "p1", title: "First pin", note: "a note", isNew: false });
    const titleInput = await screen.findByRole("textbox", { name: "Navigation pin title" });
    await user.clear(titleInput);
    await user.type(titleInput, "Edited but not saved");

    // Draft closes (e.g. committed/discarded), then a genuinely NEW draft
    // begins for a different pin - the stale local edit must not leak in.
    push(remote, ROWS);
    pushDraft(remote, { pinId: "p2", title: "Second pin", note: "", isNew: false });

    expect(await screen.findByRole("textbox", { name: "Navigation pin title" })).toHaveValue("Second pin");
  });
});
