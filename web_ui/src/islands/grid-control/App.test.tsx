import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialGridControlState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    setGridSize: vi.fn(),
    setGridOpacityPercent: vi.fn(),
    setGridStyle: vi.fn(),
    setGridColor: vi.fn(),
    setSnapToGrid: vi.fn(),
    setOrthogonalConnections: vi.fn(),
    setSmartGuides: vi.fn(),
    setFadeConnections: vi.fn(),
    resize: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { gridControlBridge: remote } });
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
  handler(JSON.stringify({ ...initialGridControlState, revision: 1, ...overrides }));
}

describe("App against a real (faked) QWebChannel connection", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("renders the size/style/color presets from state, highlighting the current ones", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote, { gridSize: 20, gridStyle: "Lines" });

    expect(await screen.findByText("20px")).toHaveClass("active");
    expect(screen.getByText("10px")).not.toHaveClass("active");
    expect(screen.getByText("Lines")).toHaveClass("active");
    expect(screen.getByText("Dots")).not.toHaveClass("active");
  });

  it("clicking a size preset calls setGridSize", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByText("10px");

    await user.click(screen.getByText("50px"));

    expect(remote.setGridSize).toHaveBeenCalledWith(50);
  });

  it("clicking a style preset calls setGridStyle", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByText("Cross");

    await user.click(screen.getByText("Cross"));

    expect(remote.setGridStyle).toHaveBeenCalledWith("Cross");
  });

  it("clicking a color swatch calls setGridColor", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote, { colorPresets: ["#111111", "#222222"] });
    await screen.findByLabelText("Grid color #222222");

    await user.click(screen.getByLabelText("Grid color #222222"));

    expect(remote.setGridColor).toHaveBeenCalledWith("#222222");
  });

  it("moving the opacity slider calls setGridOpacityPercent", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    push(remote, { gridOpacityPercent: 30 });
    const slider = await screen.findByLabelText("Grid opacity");

    fireEvent.change(slider, { target: { value: "75" } });

    expect(remote.setGridOpacityPercent).toHaveBeenCalledWith(75);
  });

  it("toggling Snap to Grid checkbox calls setSnapToGrid and keeps the box checked", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    const checkbox = await screen.findByRole("checkbox", { name: "Snap to Grid" });

    await user.click(checkbox);

    expect(remote.setSnapToGrid).toHaveBeenCalledWith(true);
    expect(checkbox).toBeChecked();
  });

  it("toggling the other 3 checkboxes calls their matching intents", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByRole("checkbox", { name: "Snap to Grid" });

    await user.click(screen.getByRole("checkbox", { name: "Orthogonal Connections" }));
    await user.click(screen.getByRole("checkbox", { name: "Smart Guides" }));
    await user.click(screen.getByRole("checkbox", { name: "Faded Connections" }));

    expect(remote.setOrthogonalConnections).toHaveBeenCalledWith(true);
    expect(remote.setSmartGuides).toHaveBeenCalledWith(true);
    expect(remote.setFadeConnections).toHaveBeenCalledWith(true);
  });

  it("renders the shared error state on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const handler = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    handler(JSON.stringify({ ...initialGridControlState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("The grid control panel is unavailable")).toBeInTheDocument();
  });
});
