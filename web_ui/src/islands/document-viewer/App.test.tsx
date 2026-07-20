import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { initialDocumentViewerState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

// jsdom has no window.QWebChannel, so createDocumentViewerBridge() falls
// through to the mock bridge automatically for the smoke tests below - same
// pattern as every other island's App.test.tsx.

describe("App against the mock bridge", () => {
  it("renders the empty-content placeholder before anything has been shown", () => {
    render(<App />);

    expect(screen.getByText("No document content is available yet.")).toBeInTheDocument();
  });

  it("clicking Close does not throw against the mock bridge", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Close" }));
  });
});

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    close: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { documentViewerBridge: remote } });
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

  it("renders markdown headings, a fenced code block (highlighted), and a GFM table", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    const content = [
      "## Code",
      "",
      "```python",
      "print('hi')",
      "```",
      "",
      "| Col A | Col B |",
      "| --- | --- |",
      "| one | two |",
    ].join("\n");
    push(JSON.stringify({ ...initialDocumentViewerState, content }));

    expect(await screen.findByRole("heading", { name: "Code" })).toBeInTheDocument();
    // rehype-highlight tags the fenced block's <code> with a hljs/language
    // class and splits tokens into their own <span>s - textContent joins them
    // back into the original source line.
    const codeBlock = document.querySelector("code.hljs.language-python");
    expect(codeBlock).not.toBeNull();
    expect(codeBlock?.textContent).toBe("print('hi')\n");
    expect(document.querySelector(".hljs-built_in")?.textContent).toBe("print");
    expect(document.querySelector(".hljs-string")?.textContent).toBe("'hi'");
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("one")).toBeInTheDocument();
    expect(screen.getByText("two")).toBeInTheDocument();
  });

  it("clicking Close calls through to the remote's close()", async () => {
    const remote = installFakeQWebChannel();
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByRole("button", { name: "Close" }));

    expect(remote.close).toHaveBeenCalledTimes(1);
  });

  it("renders the shared error state on a rejected payload", async () => {
    const remote = installFakeQWebChannel();
    render(<App />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    push(JSON.stringify({ ...initialDocumentViewerState, minCompatibleSchemaVersion: 999 }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Document View is unavailable")).toBeInTheDocument();
  });
});
