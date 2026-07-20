import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialAboutState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

// jsdom has no window.QWebChannel, so createAboutBridge() falls through to
// the mock bridge automatically for the smoke test below - same pattern as
// every other island's App.test.tsx.

describe("App against the mock bridge", () => {
  it("renders the app name, version, and developer credits from the initial state", () => {
    render(<App />);

    expect(screen.getByText(initialAboutState.appName)).toBeInTheDocument();
    expect(screen.getByText(`Version ${initialAboutState.appVersion}`)).toBeInTheDocument();
    expect(screen.getByText(initialAboutState.developerName)).toBeInTheDocument();
    expect(screen.getByText(initialAboutState.copyrightText)).toBeInTheDocument();
  });

  it("renders the 3 link buttons and the Close button", () => {
    render(<App />);

    expect(screen.getByRole("button", { name: "Graphlink Repository" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Personal Webpage" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Personal GitHub" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  });

  it("clicking a link button and Close do not throw against the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Graphlink Repository" }));
    await user.click(screen.getByRole("button", { name: "Close" }));
  });
});

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    close: vi.fn(),
    openExternal: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { aboutBridge: remote } });
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

  it("clicking the Graphlink Repository button calls through to openExternal with its URL", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(
      JSON.stringify({
        ...initialAboutState,
        repositoryUrl: "https://github.com/dovvnloading/Graphlink",
      }),
    );

    await user.click(await screen.findByRole("button", { name: "Graphlink Repository" }));

    expect(remote.openExternal).toHaveBeenCalledWith("https://github.com/dovvnloading/Graphlink");
  });

  it("clicking Personal Webpage and Personal GitHub call through with their own URLs", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
    push(
      JSON.stringify({
        ...initialAboutState,
        developerWebsiteUrl: "https://mattwesney.com",
        developerGithubUrl: "https://github.com/dovvnloading",
      }),
    );

    await user.click(await screen.findByRole("button", { name: "Personal Webpage" }));
    await user.click(screen.getByRole("button", { name: "Personal GitHub" }));

    expect(remote.openExternal).toHaveBeenCalledWith("https://mattwesney.com");
    expect(remote.openExternal).toHaveBeenCalledWith("https://github.com/dovvnloading");
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

    push(JSON.stringify({ ...initialAboutState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("About is unavailable")).toBeInTheDocument();
  });
});
