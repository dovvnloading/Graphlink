import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialNotificationState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

// jsdom has no window.QWebChannel, so createNotificationBridge() falls
// through to the mock bridge automatically for the smoke test below - same
// pattern as ComposerApp.test.tsx/token-counter's App.test.tsx.

describe("App against the mock bridge", () => {
  it("starts hidden, matching the initial visible:false state", () => {
    render(<App />);
    expect(screen.getByRole("status", { hidden: true })).not.toBeVisible();
  });
});

// The mock bridge is deliberately inert (copyDetails/dismiss are no-ops -
// Python is fully authoritative for visibility, see bridge.ts's docstring),
// so interactive behavior needs a real (faked) QWebChannel connection to
// drive real state through, same approach as
// composer/ComposerApp.reject-recover.test.tsx.

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    copyDetails: vi.fn(),
    dismiss: vi.fn(),
    resize: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { notificationBridge: remote } });
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

  it("renders the message and type-specific title once Python publishes a visible state", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    push(
      JSON.stringify({
        ...initialNotificationState,
        visible: true,
        message: "Careful, this cannot be undone.",
        msgType: "warning",
      }),
    );

    await waitFor(() => expect(screen.getByRole("status")).toBeVisible());
    expect(screen.getByText("Warning")).toBeInTheDocument();
    expect(screen.getByText("Careful, this cannot be undone.")).toBeInTheDocument();
  });

  it("Dismiss and the close button both call through to the remote's dismiss()", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(JSON.stringify({ ...initialNotificationState, visible: true, message: "Hi.", msgType: "info" }));
    await waitFor(() => expect(screen.getByRole("status")).toBeVisible());

    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    await user.click(screen.getByRole("button", { name: "Dismiss notification" }));

    expect(remote.dismiss).toHaveBeenCalledTimes(2);
  });

  it("Copy details calls through to the remote and shows local 'Copied' feedback", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(
      JSON.stringify({
        ...initialNotificationState,
        visible: true,
        message: "Copy this text.",
        msgType: "info",
      }),
    );
    await waitFor(() => expect(screen.getByRole("status")).toBeVisible());

    await user.click(screen.getByRole("button", { name: "Copy details" }));

    expect(remote.copyDetails).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  // Confirms App.tsx actually reaches the shared lib/ui/BridgeErrorState on a
  // rejected payload, with this island's own title/className - the shared
  // component's own rendering logic is covered by
  // lib/ui/BridgeErrorState.test.tsx, this only proves the wiring at this
  // specific call site is correct.
  it("renders the shared error state on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    push(JSON.stringify({ ...initialNotificationState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Notifications unavailable")).toBeInTheDocument();
  });
});
