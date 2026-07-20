import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialSettingsState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

// jsdom has no window.QWebChannel, so createSettingsBridge() falls through
// to the mock bridge automatically for the smoke test below - same pattern
// as every other island's App.test.tsx.

describe("App against the mock bridge", () => {
  it("renders all 5 rail sections with General active", () => {
    render(<App />);

    expect(screen.getByRole("button", { name: "General" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Ollama (Local)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Llama.cpp (Local)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "API Endpoint" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Integrations" })).toBeInTheDocument();
  });

  it("clicking a rail button navigates to that section", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Integrations" }));

    expect(screen.getByRole("button", { name: "Integrations" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "General" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("region", { name: "Integrations" })).toBeInTheDocument();
  });
});

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    setActiveSection: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { settingsBridge: remote } });
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

  it("renders the section Python publishes as active", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    push(JSON.stringify({ ...initialSettingsState, activeSection: "API Endpoint", revision: 1 }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "API Endpoint" })).toHaveAttribute("aria-current", "page"),
    );
  });

  it("clicking a rail button calls through to setActiveSection", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Ollama (Local)" }));

    expect(remote.setActiveSection).toHaveBeenCalledWith("Ollama (Local)");
  });

  // Confirms App.tsx actually reaches the shared lib/ui/BridgeErrorState on
  // a rejected payload, with this island's own title/className - the
  // shared component's own rendering logic is covered by
  // lib/ui/BridgeErrorState.test.tsx, this only proves the wiring at this
  // specific call site is correct.
  it("renders the shared error state on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    push(JSON.stringify({ ...initialSettingsState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Settings unavailable")).toBeInTheDocument();
  });
});
