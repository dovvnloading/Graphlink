import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialCommandPaletteState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

// jsdom has no window.QWebChannel, so createCommandPaletteBridge() falls
// through to the mock bridge automatically for the smoke test below - same
// pattern as notification/App.test.tsx.

describe("App against the mock bridge", () => {
  it("starts hidden, matching the initial visible:false state", () => {
    render(<App />);
    expect(screen.getByRole("dialog", { hidden: true })).not.toBeVisible();
  });
});

// The mock bridge is deliberately inert (there is no JS-callable "open" -
// only Python's show_command_palette() ever opens the palette), so
// interactive behavior needs a real (faked) QWebChannel connection, same
// approach as notification/App.test.tsx.

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    executeCommand: vi.fn(),
    dismiss: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { commandPaletteBridge: remote } });
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

const openPayload = (overrides: Partial<typeof initialCommandPaletteState> = {}) =>
  JSON.stringify({
    ...initialCommandPaletteState,
    visible: true,
    revision: 1,
    commands: [
      { id: "0", name: "New Chat", aliases: ["new chat", "start new"] },
      { id: "1", name: "Reset View", aliases: ["reset view", "reset zoom"] },
    ],
    ...overrides,
  });

describe("App against a real (faked) QWebChannel connection", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("renders every command once Python publishes a visible state", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    push(openPayload());

    await waitFor(() => expect(screen.getByRole("dialog")).toBeVisible());
    expect(screen.getByText("New Chat")).toBeInTheDocument();
    expect(screen.getByText("Reset View")).toBeInTheDocument();
  });

  it("filters client-side against aliases as the user types, with no bridge calls", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(openPayload());
    await waitFor(() => expect(screen.getByRole("dialog")).toBeVisible());

    await user.type(screen.getByLabelText("Search commands"), "zoom");

    expect(screen.getByText("Reset View")).toBeInTheDocument();
    expect(screen.queryByText("New Chat")).not.toBeInTheDocument();
    expect(remote.executeCommand).not.toHaveBeenCalled();
  });

  it("Enter executes the currently-selected (first) filtered command", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(openPayload());
    await waitFor(() => expect(screen.getByRole("dialog")).toBeVisible());

    await user.type(screen.getByLabelText("Search commands"), "{Enter}");

    expect(remote.executeCommand).toHaveBeenCalledWith("0");
  });

  it("ArrowDown then Enter executes the second command", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(openPayload());
    await waitFor(() => expect(screen.getByRole("dialog")).toBeVisible());

    await user.type(screen.getByLabelText("Search commands"), "{ArrowDown}{Enter}");

    expect(remote.executeCommand).toHaveBeenCalledWith("1");
  });

  it("clicking a result executes it directly", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(openPayload());
    await waitFor(() => expect(screen.getByRole("dialog")).toBeVisible());

    await user.click(screen.getByText("Reset View"));

    expect(remote.executeCommand).toHaveBeenCalledWith("1");
  });

  it("Escape calls dismiss", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(openPayload());
    await waitFor(() => expect(screen.getByRole("dialog")).toBeVisible());

    await user.type(screen.getByLabelText("Search commands"), "{Escape}");

    expect(remote.dismiss).toHaveBeenCalledTimes(1);
  });

  it("shows a stale-command notice and clears it on the next keystroke", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(openPayload());
    await waitFor(() => expect(screen.getByRole("dialog")).toBeVisible());

    push(openPayload({ revision: 2, notice: "That command is no longer available." }));
    await waitFor(() =>
      expect(screen.getByText("That command is no longer available.")).toBeInTheDocument(),
    );

    await user.type(screen.getByLabelText("Search commands"), "x");

    expect(screen.queryByText("That command is no longer available.")).not.toBeInTheDocument();
  });

  it("a fresh open (visible false -> true) resets a leftover query", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(openPayload());
    await waitFor(() => expect(screen.getByRole("dialog")).toBeVisible());
    await user.type(screen.getByLabelText("Search commands"), "zoom");
    expect((screen.getByLabelText("Search commands") as HTMLInputElement).value).toBe("zoom");

    push(JSON.stringify({ ...initialCommandPaletteState, visible: false, revision: 2 }));
    await waitFor(() => expect(screen.getByRole("dialog", { hidden: true })).not.toBeVisible());
    push(openPayload({ revision: 3 }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeVisible());

    expect((screen.getByLabelText("Search commands") as HTMLInputElement).value).toBe("");
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

    push(JSON.stringify({ ...initialCommandPaletteState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Command palette unavailable")).toBeInTheDocument();
  });
});
