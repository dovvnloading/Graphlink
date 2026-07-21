import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialComposerContextState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

const ANCHOR = { id: "node-1", label: "Root cause analysis", type: "Chat node" };
const ITEMS = [
  { id: "attachment-1", name: "notes.txt", kind: "document", tokenCount: 120 },
  { id: "attachment-2", name: "diagram.png", kind: "image", tokenCount: 300 },
];

describe("App against the mock bridge", () => {
  afterEach(cleanup);

  it("renders zero tokens and no list when there is no context", () => {
    render(<App />);

    expect(screen.getByText("Estimated context · 0 tokens")).toBeInTheDocument();
  });
});

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    removeContextItem: vi.fn(),
    resize: vi.fn(),
    close: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { composerContextBridge: remote } });
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
  handler(JSON.stringify({ ...initialComposerContextState, revision: 1, ...overrides }));
}

describe("App against a real (faked) QWebChannel connection", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("renders the anchor row (non-removable) and each item row (removable) plus the token total", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote, { anchor: ANCHOR, items: ITEMS, totalTokens: 420 });

    expect(await screen.findByText("Root cause analysis")).toBeInTheDocument();
    expect(screen.getByText("notes.txt")).toBeInTheDocument();
    expect(screen.getByText("diagram.png")).toBeInTheDocument();
    expect(screen.getByText("Estimated context · 420 tokens")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove Root cause analysis" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove notes.txt" })).toBeInTheDocument();
  });

  it("clicking Remove on an item calls removeContextItem with its id", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote, { anchor: ANCHOR, items: ITEMS, totalTokens: 420 });
    await screen.findByText("notes.txt");

    await user.click(screen.getByRole("button", { name: "Remove notes.txt" }));

    expect(remote.removeContextItem).toHaveBeenCalledWith("attachment-1");
  });

  it("Close button calls close", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote, { anchor: ANCHOR, items: ITEMS, totalTokens: 420 });
    await screen.findByText("notes.txt");

    await user.click(screen.getByRole("button", { name: "Close context review" }));

    expect(remote.close).toHaveBeenCalledTimes(1);
  });

  it("Escape anywhere in the surface calls close", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    push(remote, { anchor: ANCHOR, items: ITEMS, totalTokens: 420 });
    await screen.findByText("notes.txt");

    const escape = new KeyboardEvent("keydown", { key: "Escape" });
    window.dispatchEvent(escape);

    expect(remote.close).toHaveBeenCalledTimes(1);
  });

  it("renders the shared error state on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const handler = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    handler(JSON.stringify({ ...initialComposerContextState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Context review is unavailable")).toBeInTheDocument();
  });
});
