import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialHelpState } from "./bridgeTypes";
import { HELP_SECTIONS } from "./data/sections";
import type { QtWindow } from "../../lib/bridge-core/transport";

// jsdom has no window.QWebChannel, so createHelpBridge() falls through to
// the mock bridge automatically for the smoke test below - same pattern as
// every other island's App.test.tsx.

describe("App against the mock bridge", () => {
  it("renders all 9 rail sections with Overview active by default", () => {
    render(<App />);

    for (const section of HELP_SECTIONS) {
      expect(screen.getByRole("button", { name: section.name })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "Overview" })).toHaveAttribute("aria-current", "page");
  });

  it("renders the active section's heading, description, and item cards", () => {
    render(<App />);

    const overview = HELP_SECTIONS[0];
    expect(screen.getByRole("heading", { name: overview.name })).toBeInTheDocument();
    expect(screen.getByText(overview.description)).toBeInTheDocument();
    expect(screen.getByText(overview.subsections[0].items[0].action)).toBeInTheDocument();
  });

  it("clicking a rail button switches the active section without a bridge round-trip", async () => {
    const user = userEvent.setup();
    render(<App />);
    const secondSection = HELP_SECTIONS[1];

    await user.click(screen.getByRole("button", { name: secondSection.name }));

    expect(screen.getByRole("button", { name: secondSection.name })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Overview" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("heading", { name: secondSection.name })).toBeInTheDocument();
  });

  it("clicking Close does not throw against the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Close" }));
  });
});

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    close: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { helpBridge: remote } });
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

describe("App against a real (faked) QWebChannel connection", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("clicking Close calls through to the remote's close()", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Close" }));

    expect(remote.close).toHaveBeenCalledTimes(1);
  });

  it("pressing Escape calls through to the remote's close()", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);

    await user.keyboard("{Escape}");

    expect(remote.close).toHaveBeenCalledTimes(1);
  });

  it("renders the shared error state on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    push(JSON.stringify({ ...initialHelpState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Help is unavailable")).toBeInTheDocument();
  });
});
