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

  it("General renders the theme select and all 4 notification checkboxes, all reflecting initial state", () => {
    render(<App />);

    expect(screen.getByLabelText("Theme")).toHaveValue("dark");
    expect(screen.getByRole("checkbox", { name: "Show Token Counter Overlay" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Enable Assistant System Prompt" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Info" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Success" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Warning" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Error" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Enable Update Notifications on Startup" })).not.toBeChecked();
    expect(screen.getByText("Automatic update checks are off.")).toBeInTheDocument();
  });

  it("changing the theme select updates state via the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.selectOptions(screen.getByLabelText("Theme"), "muted");

    expect(screen.getByLabelText("Theme")).toHaveValue("muted");
  });

  it("unchecking a notification type updates only that type", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("checkbox", { name: "Warning" }));

    expect(screen.getByRole("checkbox", { name: "Warning" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Info" })).toBeChecked();
  });
});

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    setActiveSection: vi.fn(),
    setTheme: vi.fn(),
    setShowTokenCounter: vi.fn(),
    setEnableSystemPrompt: vi.fn(),
    setNotificationPreference: vi.fn(),
    setUpdateNotificationsEnabled: vi.fn(),
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

  it("toggling a General/Appearance checkbox calls through to the remote", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("checkbox", { name: "Show Token Counter Overlay" }));

    expect(remote.setShowTokenCounter).toHaveBeenCalledWith(false);
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
