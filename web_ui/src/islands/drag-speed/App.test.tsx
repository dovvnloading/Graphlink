import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialDragSpeedState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    setDragFactor: vi.fn(),
    resize: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { dragSpeedBridge: remote } });
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
  handler(JSON.stringify({ ...initialDragSpeedState, revision: 1, ...overrides }));
}

describe("App against a real (faked) QWebChannel connection", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("renders every preset from state, highlighting the default 100% value", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote);

    expect(await screen.findByText("100%")).toHaveClass("active");
    expect(screen.getByText("25%")).not.toHaveClass("active");
  });

  it("clicking a preset calls setDragFactor with the preset divided by 100", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByText("50%");

    await user.click(screen.getByText("50%"));

    expect(remote.setDragFactor).toHaveBeenCalledWith(0.5);
  });

  it("moving the slider calls setDragFactor with the slider value divided by 100", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    push(remote, { percentMin: 10, percentMax: 100 });
    const slider = await screen.findByLabelText("Drag speed");

    fireEvent.change(slider, { target: { value: "40" } });

    expect(remote.setDragFactor).toHaveBeenCalledWith(0.4);
  });

  it("renders the shared error state on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const handler = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    handler(JSON.stringify({ ...initialDragSpeedState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("The drag speed panel is unavailable")).toBeInTheDocument();
  });
});
