import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialComposerPickerState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

const MODEL_OPTIONS = [
  { id: "gpt-4", label: "GPT-4", meta: "Selected", current: true, unavailable: false },
  { id: "gpt-3.5", label: "GPT-3.5", meta: "Available", current: false, unavailable: false },
  { id: "old-model", label: "Old Model", meta: "Available - verify in Settings", current: false, unavailable: true },
];

const REASONING_OPTIONS = [
  { id: "Quick", label: "Quick", meta: "Direct responses with less deliberation.", current: true, unavailable: false },
  { id: "Thinking", label: "Thinking", meta: "More deliberate reasoning for complex requests.", current: false, unavailable: false },
];

describe("App against the mock bridge", () => {
  afterEach(cleanup);

  it("renders the empty-catalog message and no search input when there are no options", () => {
    render(<App />);

    expect(screen.getByText("No model catalog available yet.")).toBeInTheDocument();
  });
});

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    selectOption: vi.fn(),
    requestSettings: vi.fn(),
    resize: vi.fn(),
    close: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { composerPickerBridge: remote } });
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
  handler(JSON.stringify({ ...initialComposerPickerState, revision: 1, ...overrides }));
}

describe("App against a real (faked) QWebChannel connection - model kind", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("renders each model option's label/meta, a search box, and the Current badge", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote, { kind: "model", title: "Ollama", options: MODEL_OPTIONS, openToken: 1 });

    expect(await screen.findByText("GPT-4")).toBeInTheDocument();
    expect(screen.getByText("Selected")).toBeInTheDocument();
    expect(screen.getByText("Current")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Search available models" })).toBeInTheDocument();
  });

  it("filters options client-side as the user types, with no bridge round-trip", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote, { kind: "model", title: "Ollama", options: MODEL_OPTIONS, openToken: 1 });
    await screen.findByText("GPT-4");

    await user.type(screen.getByRole("textbox", { name: "Search available models" }), "3.5");

    expect(screen.queryByText("GPT-4")).not.toBeInTheDocument();
    expect(screen.getByText("GPT-3.5")).toBeInTheDocument();
  });

  it("clicking an available row calls selectOption", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote, { kind: "model", title: "Ollama", options: MODEL_OPTIONS, openToken: 1 });
    await screen.findByText("GPT-3.5");

    await user.click(screen.getByText("GPT-3.5"));

    expect(remote.selectOption).toHaveBeenCalledWith("gpt-3.5");
  });

  it("an unavailable row is disabled and never calls selectOption", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote, { kind: "model", title: "Ollama", options: MODEL_OPTIONS, openToken: 1 });
    const row = await screen.findByRole("option", { name: /Old Model/ });

    expect(row.querySelector("button")).toBeDisabled();
    await user.click(row);
    expect(remote.selectOption).not.toHaveBeenCalled();
  });

  it("shows the settings hint only when there is no query and zero options", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote, { kind: "model", title: "Ollama", options: [], openToken: 1 });

    expect(await screen.findByText("Open Settings to discover models")).toBeInTheDocument();
  });

  it("clicking the settings hint calls requestSettings", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote, { kind: "model", title: "Ollama", options: [], openToken: 1 });
    await screen.findByText("Open Settings to discover models");

    await user.click(screen.getByText("Open Settings to discover models"));

    expect(remote.requestSettings).toHaveBeenCalledTimes(1);
  });

  it("Close button calls close", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote, { kind: "model", title: "Ollama", options: MODEL_OPTIONS, openToken: 1 });
    await screen.findByText("GPT-4");

    await user.click(screen.getByRole("button", { name: "Close selector" }));

    expect(remote.close).toHaveBeenCalledTimes(1);
  });

  it("Escape anywhere in the surface calls close", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    push(remote, { kind: "model", title: "Ollama", options: MODEL_OPTIONS, openToken: 1 });
    await screen.findByText("GPT-4");

    const escape = new KeyboardEvent("keydown", { key: "Escape" });
    window.dispatchEvent(escape);

    expect(remote.close).toHaveBeenCalledTimes(1);
  });

  it("resets the local search query when a fresh openToken arrives", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote, { kind: "model", title: "Ollama", options: MODEL_OPTIONS, openToken: 1 });
    await user.type(screen.getByRole("textbox", { name: "Search available models" }), "3.5");
    expect(screen.queryByText("GPT-4")).not.toBeInTheDocument();

    push(remote, { kind: "model", title: "Ollama", options: MODEL_OPTIONS, openToken: 2 });

    expect(await screen.findByText("GPT-4")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Search available models" })).toHaveValue("");
  });
});

describe("App against a real (faked) QWebChannel connection - reasoning kind", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("renders reasoning options with no search box", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);

    push(remote, {
      kind: "reasoning",
      title: "Choose response depth",
      options: REASONING_OPTIONS,
      openToken: 1,
    });

    expect(await screen.findByText("Thinking")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("clicking Thinking calls selectOption with its id", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);
    push(remote, {
      kind: "reasoning",
      title: "Choose response depth",
      options: REASONING_OPTIONS,
      openToken: 1,
    });
    await screen.findByText("Thinking");

    await user.click(screen.getByText("Thinking"));

    expect(remote.selectOption).toHaveBeenCalledWith("Thinking");
  });

  it("renders the shared error state on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const handler = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    handler(JSON.stringify({ ...initialComposerPickerState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("The picker is unavailable")).toBeInTheDocument();
  });
});
