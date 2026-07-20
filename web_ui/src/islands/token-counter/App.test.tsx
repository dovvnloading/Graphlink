import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import App from "./App";
import { initialTokenCounterState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

// jsdom has no window.QWebChannel, so createTokenCounterBridge() falls
// through to the mock bridge automatically - same pattern as
// ComposerApp.test.tsx.

describe("App against the mock bridge", () => {
  it("renders all four rows starting at zero", () => {
    render(<App />);

    expect(screen.getByText("Input:")).toBeInTheDocument();
    expect(screen.getByText("Output:")).toBeInTheDocument();
    expect(screen.getByText("Context:")).toBeInTheDocument();
    expect(screen.getByText("Total:")).toBeInTheDocument();
    expect(screen.getAllByText("0")).toHaveLength(4);
  });
});

// Confirms App.tsx actually reaches the shared lib/ui/BridgeErrorState on a
// rejected payload, with this island's own title/className - the shared
// component's own rendering logic is covered by lib/ui/BridgeErrorState.test.tsx,
// this only proves the wiring at this specific call site is correct.
describe("App against a real (faked) QWebChannel connection", () => {
  afterEach(() => {
    cleanup();
    const qtWindow = window as unknown as QtWindow;
    delete qtWindow.QWebChannel;
    delete qtWindow.qt;
  });

  it("renders the shared error state on a rejected payload", async () => {
    const remote = {
      stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
      ready: vi.fn(),
    };
    class FakeQWebChannel {
      constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
        cb({ objects: { tokenCounterBridge: remote } });
      }
    }
    const qtWindow = window as unknown as QtWindow;
    qtWindow.QWebChannel = FakeQWebChannel as unknown as QtWindow["QWebChannel"];
    qtWindow.qt = { webChannelTransport: {} };

    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    push(JSON.stringify({ ...initialTokenCounterState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Token counter unavailable")).toBeInTheDocument();
  });
});
