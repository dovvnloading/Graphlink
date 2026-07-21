import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialFontControlState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

const FAMILIES = ["Segoe UI", "Arial", "Consolas"];
const COLOR_PRESETS = ["#F0F0F0", "#C7C7C7", "#949494", "#818181"];

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    setFontFamily: vi.fn(),
    setFontSize: vi.fn(),
    setFontColor: vi.fn(),
    resize: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { fontControlBridge: remote } });
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
      ...initialFontControlState,
      revision: 1,
      fontFamilies: FAMILIES,
      colorPresets: COLOR_PRESETS,
      ...overrides,
    }),
  );
}

describe("App against a real (faked) QWebChannel connection", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("renders every font family option and every color swatch from state", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote);

    expect(await screen.findByRole("option", { name: "Consolas" })).toBeInTheDocument();
    expect(screen.getByLabelText("Font color #818181")).toBeInTheDocument();
  });

  it("selecting a family calls setFontFamily", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByRole("option", { name: "Consolas" });

    await user.selectOptions(screen.getByLabelText("Font family"), "Consolas");

    expect(remote.setFontFamily).toHaveBeenCalledWith("Consolas");
  });

  it("moving the size slider calls setFontSize", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    push(remote, { sizeMin: 8, sizeMax: 16 });
    const slider = await screen.findByLabelText("Font size");

    fireEvent.change(slider, { target: { value: "14" } });

    expect(remote.setFontSize).toHaveBeenCalledWith(14);
  });

  it("clicking a color swatch calls setFontColor", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote);
    await screen.findByLabelText("Font color #C7C7C7");

    await user.click(screen.getByLabelText("Font color #C7C7C7"));

    expect(remote.setFontColor).toHaveBeenCalledWith("#C7C7C7");
  });

  it("renders the shared error state on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const handler = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    handler(JSON.stringify({ ...initialFontControlState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("The font control panel is unavailable")).toBeInTheDocument();
  });
});
