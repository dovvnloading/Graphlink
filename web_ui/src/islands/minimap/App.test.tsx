import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialMinimapState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

const NODES = [
  { id: "1", isUser: true, preview: "Hello there" },
  { id: "2", isUser: false, preview: "Hi back" },
];

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    selectNode: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { minimapBridge: remote } });
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
  handler(JSON.stringify({ ...initialMinimapState, revision: 1, nodes: NODES, ...overrides }));
}

describe("App against a real (faked) QWebChannel connection", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("renders one indicator per node, colored by isUser, with the preview as its accessible name", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote);

    const userIndicator = await screen.findByRole("listitem", { name: "Hello there" });
    const aiIndicator = screen.getByRole("listitem", { name: "Hi back" });
    expect(userIndicator).toHaveClass("user");
    expect(aiIndicator).toHaveClass("ai");
  });

  it("clicking an indicator calls selectNode with its id", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    const indicator = await screen.findByRole("listitem", { name: "Hi back" });

    await user.click(indicator);

    expect(remote.selectNode).toHaveBeenCalledWith("2");
  });

  it("renders nothing in the list when there are no nodes", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote, { nodes: [] });

    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  });

  it("renders the shared error state on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const handler = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    handler(JSON.stringify({ ...initialMinimapState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("The minimap is unavailable")).toBeInTheDocument();
  });
});
