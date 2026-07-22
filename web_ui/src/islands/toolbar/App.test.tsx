import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialToolbarState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    reportAnchorRect: vi.fn(),
    openLibrary: vi.fn(),
    saveChat: vi.fn(),
    togglePins: vi.fn(),
    organizeNodes: vi.fn(),
    zoomIn: vi.fn(),
    zoomOut: vi.fn(),
    resetZoom: vi.fn(),
    fitAll: vi.fn(),
    toggleControls: vi.fn(),
    togglePlugins: vi.fn(),
    selectMode: vi.fn(),
    openSettings: vi.fn(),
    openAbout: vi.fn(),
    openHelp: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { toolbarBridge: remote } });
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
  handler(
    JSON.stringify({
      ...initialToolbarState,
      revision: 1,
      modeOptions: ["Ollama (Local)", "llama.cpp (Local)", "API Endpoint"],
      currentMode: "Ollama (Local)",
      ...overrides,
    }),
  );
}

describe("App against the mock bridge", () => {
  afterEach(cleanup);

  it("renders every one of the 14 toolbar intents", () => {
    render(<App />);

    for (const label of [
      "Library",
      "Save",
      "Pins",
      "Organize",
      "Zoom In",
      "Zoom Out",
      "Reset",
      "Fit All",
      "Controls",
      "About",
      "Settings",
      "Help",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: /Plugins/ })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Provider mode" })).toBeInTheDocument();
  });
});

describe("App against a real (faked) QWebChannel connection", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("reports all 4 anchor rects on mount", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    await screen.findByRole("button", { name: "Library" });

    const reportedNames = remote.reportAnchorRect.mock.calls.map((call) => call[0]);
    expect(reportedNames).toEqual(expect.arrayContaining(["pins", "plugins", "settings", "help"]));
  });

  it("pinsChecked reflects the server-published state, not local click state", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    push(remote, { pinsChecked: false });

    const pinsButton = await screen.findByRole("button", { name: "Pins" });
    expect(pinsButton).toHaveAttribute("aria-pressed", "false");

    push(remote, { pinsChecked: true });
    await waitFor(() => expect(pinsButton).toHaveAttribute("aria-pressed", "true"));
  });

  it("clicking Pins calls togglePins - the button never flips itself locally", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    const pinsButton = await screen.findByRole("button", { name: "Pins" });

    await user.click(pinsButton);

    expect(remote.togglePins).toHaveBeenCalledTimes(1);
    expect(pinsButton).toHaveAttribute("aria-pressed", "false");
  });

  it("clicking Controls fires toggleControls but never flips itself locally", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    const controlsButton = await screen.findByRole("button", { name: "Controls" });
    expect(controlsButton).toHaveAttribute("aria-pressed", "false");

    await user.click(controlsButton);
    expect(remote.toggleControls).toHaveBeenCalledWith(true);
    // Server-authoritative: no payload arrived, so the chip stays unpressed.
    expect(controlsButton).toHaveAttribute("aria-pressed", "false");

    push(remote, { activeSurface: "controls" });
    await waitFor(() => expect(controlsButton).toHaveAttribute("aria-pressed", "true"));

    await user.click(controlsButton);
    expect(remote.toggleControls).toHaveBeenLastCalledWith(false);
    // Still pressed until the server republishes the close.
    expect(controlsButton).toHaveAttribute("aria-pressed", "true");
  });

  it("a chip shows active when activeSurface matches and clears when it goes back to empty", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    push(remote, { activeSurface: "" });

    const chips: Array<[string, RegExp | string]> = [
      ["library", "Library"],
      ["controls", "Controls"],
      ["plugins", /Plugins/],
      ["settings", "Settings"],
      ["about", "About"],
      ["help", "Help"],
    ];

    for (const [surface, label] of chips) {
      const chip = await screen.findByRole("button", { name: label });
      expect(chip).toHaveAttribute("aria-pressed", "false");

      push(remote, { activeSurface: surface });
      await waitFor(() => expect(chip).toHaveAttribute("aria-pressed", "true"));
      expect(chip.className).toContain("checked");

      push(remote, { activeSurface: "" });
      await waitFor(() => expect(chip).toHaveAttribute("aria-pressed", "false"));
      expect(chip.className).not.toContain("checked");
    }
  });

  it("only the chip matching activeSurface is pressed, never its siblings", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    push(remote, { activeSurface: "settings" });

    const settings = await screen.findByRole("button", { name: "Settings" });
    await waitFor(() => expect(settings).toHaveAttribute("aria-pressed", "true"));
    for (const label of ["Library", "Controls", "About", "Help"]) {
      expect(screen.getByRole("button", { name: label })).toHaveAttribute("aria-pressed", "false");
    }
    expect(screen.getByRole("button", { name: /Plugins/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("the mode select shows the real options and calls selectMode on change", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    const select = await screen.findByRole("combobox", { name: "Provider mode" });
    expect(select).toHaveValue("Ollama (Local)");

    await user.selectOptions(select, "API Endpoint");

    expect(remote.selectMode).toHaveBeenCalledWith("API Endpoint");
  });

  it("every simple action button calls its matching intent exactly once", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByRole("button", { name: "Library" });

    const cases: Array<[string, keyof Remote]> = [
      ["Library", "openLibrary"],
      ["Save", "saveChat"],
      ["Organize", "organizeNodes"],
      ["Zoom In", "zoomIn"],
      ["Zoom Out", "zoomOut"],
      ["Reset", "resetZoom"],
      ["Fit All", "fitAll"],
      ["About", "openAbout"],
      ["Settings", "openSettings"],
      ["Help", "openHelp"],
    ];

    for (const [label, method] of cases) {
      await user.click(screen.getByRole("button", { name: label }));
      expect(remote[method]).toHaveBeenCalledTimes(1);
    }
  });

  it("clicking Plugins calls togglePlugins", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByRole("button", { name: /Plugins/ });

    await user.click(screen.getByRole("button", { name: /Plugins/ }));

    expect(remote.togglePlugins).toHaveBeenCalledTimes(1);
  });

  it("renders the shared error state on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const handler = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    handler(JSON.stringify({ ...initialToolbarState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("The toolbar is unavailable")).toBeInTheDocument();
  });
});
