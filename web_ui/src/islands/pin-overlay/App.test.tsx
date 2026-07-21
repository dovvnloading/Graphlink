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
