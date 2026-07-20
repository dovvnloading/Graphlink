import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialSearchOverlayState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

describe("App against the mock bridge", () => {
  afterEach(cleanup);

  it("renders 0 / 0 before any search", () => {
    render(<App />);

    expect(screen.getByText("0 / 0")).toBeInTheDocument();
  });

  it("typing does not throw against the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByRole("textbox", { name: "Search the canvas" }), "hi");
  });
});

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    search: vi.fn(),
    next: vi.fn(),
    previous: vi.fn(),
    close: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { searchOverlayBridge: remote } });
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

function push(remote: Remote, overrides: Record<string, unknown> = {}) {
  const handler = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
  handler(JSON.stringify({ ...initialSearchOverlayState, revision: 1, ...overrides }));
}

describe("App against a real (faked) QWebChannel connection", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("typing calls bridge.search with the raw text", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByRole("textbox", { name: "Search the canvas" }), "hi");

    expect(remote.search).toHaveBeenLastCalledWith("hi");
  });

  it("renders '1 / 3' with the active tone once a match is focused", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote, { currentIndex: 0, totalMatches: 3 });

    const count = await screen.findByText("1 / 3");
    expect(count).toHaveAttribute("data-tone", "active");
  });

  it("renders '0 / 3' with the idle tone right after a fresh search, before navigating", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote, { currentIndex: -1, totalMatches: 3 });

    const count = await screen.findByText("0 / 3");
    expect(count).toHaveAttribute("data-tone", "idle");
  });

  it("renders '0 / 0' with the error tone when there are no matches", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote, { currentIndex: -1, totalMatches: 0 });

    const count = await screen.findByText("0 / 0");
    expect(count).toHaveAttribute("data-tone", "error");
  });

  it("Enter calls next(), Shift+Enter calls previous(), Escape calls close()", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const input = screen.getByRole("textbox", { name: "Search the canvas" });

    await user.click(input);
    await user.keyboard("{Enter}");
    expect(remote.next).toHaveBeenCalledTimes(1);

    await user.keyboard("{Shift>}{Enter}{/Shift}");
    expect(remote.previous).toHaveBeenCalledTimes(1);

    await user.keyboard("{Escape}");
    expect(remote.close).toHaveBeenCalledTimes(1);
  });

  it("the prev/next/close buttons call through to the remote", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Previous match (Shift+Enter)" }));
    await user.click(screen.getByRole("button", { name: "Next match (Enter)" }));
    await user.click(screen.getByRole("button", { name: "Close (Esc)" }));

    expect(remote.previous).toHaveBeenCalledTimes(1);
    expect(remote.next).toHaveBeenCalledTimes(1);
    expect(remote.close).toHaveBeenCalledTimes(1);
  });

  it("renders the shared error state on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const handler = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    handler(JSON.stringify({ ...initialSearchOverlayState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Search is unavailable")).toBeInTheDocument();
  });
});
