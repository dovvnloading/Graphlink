import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialPluginPickerState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

const CATEGORIES = [
  {
    name: "Branch Foundations",
    description: "Core branch scaffolding.",
    plugins: [
      { name: "System Prompt", description: "Adds a special node to override the default system prompt." },
      { name: "Conversation Node", description: "Adds a node for a self-contained, linear chat conversation." },
    ],
  },
  {
    name: "Build & Execution",
    description: "Code generation and execution tools.",
    plugins: [{ name: "Py-Coder", description: "Opens a Python execution environment." }],
  },
];

describe("App against the mock bridge", () => {
  afterEach(cleanup);

  it("renders the empty message when there are no categories", () => {
    render(<App />);

    expect(screen.getByText("No plugins are available.")).toBeInTheDocument();
  });
});

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    executePlugin: vi.fn(),
    resize: vi.fn(),
    close: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { pluginPickerBridge: remote } });
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
  handler(JSON.stringify({ ...initialPluginPickerState, revision: 1, ...overrides }));
}

describe("App against a real (faked) QWebChannel connection", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("defaults to the first category and renders its plugins", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote, { categories: CATEGORIES });

    expect(await screen.findByText("System Prompt")).toBeInTheDocument();
    expect(screen.getByText("Conversation Node")).toBeInTheDocument();
    expect(screen.queryByText("Py-Coder")).not.toBeInTheDocument();
    expect(screen.getByText("Branch Foundations", { selector: "p" })).toBeInTheDocument();
    expect(screen.getByText("2 plugins")).toBeInTheDocument();
  });

  it("clicking a category switches the visible plugin list", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote, { categories: CATEGORIES });
    await screen.findByText("System Prompt");

    await user.click(screen.getByRole("button", { name: "Build & Execution" }));

    expect(await screen.findByText("Py-Coder")).toBeInTheDocument();
    expect(screen.queryByText("System Prompt")).not.toBeInTheDocument();
    expect(screen.getByText("1 plugin")).toBeInTheDocument();
  });

  it("clicking a plugin row calls executePlugin with its name", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote, { categories: CATEGORIES });
    await screen.findByText("Conversation Node");

    await user.click(screen.getByText("Conversation Node"));

    expect(remote.executePlugin).toHaveBeenCalledWith("Conversation Node");
  });

  it("falls back to the first category if the remembered one disappears from a later publish", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote, { categories: CATEGORIES });
    await screen.findByText("System Prompt");
    await user.click(screen.getByRole("button", { name: "Build & Execution" }));
    await screen.findByText("Py-Coder");

    push(remote, { revision: 2, categories: [CATEGORIES[0]] });

    expect(await screen.findByText("System Prompt")).toBeInTheDocument();
  });

  it("Escape anywhere in the surface calls close", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    push(remote, { categories: CATEGORIES });
    await screen.findByText("System Prompt");

    const escape = new KeyboardEvent("keydown", { key: "Escape" });
    window.dispatchEvent(escape);

    expect(remote.close).toHaveBeenCalledTimes(1);
  });

  it("renders the shared error state on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const handler = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    handler(JSON.stringify({ ...initialPluginPickerState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("The plugin picker is unavailable")).toBeInTheDocument();
  });
});
