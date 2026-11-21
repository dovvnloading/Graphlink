import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import { ChatVersionCompareDialog, type ChatVersionCompareTarget } from "./ChatVersionCompareDialog";
import type { SceneStore, ChatVersionComparison } from "./sceneStore";
import { diffChatVersionWords, type ChatWordDiff } from "./chatVersionWordDiff";

const REPO_SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

class FakeDiffWorker {
  static created = 0;
  constructor() { FakeDiffWorker.created++; }
  onmessage: ((event: MessageEvent<ChatWordDiff>) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  terminate = vi.fn();

  postMessage(input: { left: string; right: string }) {
    queueMicrotask(() => {
      this.onmessage?.({ data: diffChatVersionWords(input.left, input.right) } as MessageEvent<ChatWordDiff>);
    });
  }
}

const versions = [
  { index: 0, content: "one yellow three", provider: "Provider A", model: "Model 1",
    createdAt: "2026-01-01T12:00:00Z" },
  { index: 1, content: "one blue three", provider: "Provider B", model: "Model 2",
    createdAt: "2026-02-02T12:00:00Z" },
  { index: 2, content: "one green three", provider: "Provider C", model: "Model 3",
    createdAt: "2026-03-03T12:00:00Z" },
];

const target: ChatVersionCompareTarget = {
  nodeId: "reply-1", versionCount: 3, selectedIndex: 2, requestId: 1,
};

function Launcher({ store }: { store: SceneStore }) {
  const overlays = useOverlays();
  return (
    <>
      <button type="button" onClick={() => overlays.open("chat-version-compare", "dialog")}>
        Open comparison
      </button>
      <ChatVersionCompareDialog store={store} target={target} />
    </>
  );
}

function setupStore() {
  const compareChatVersions = vi.fn().mockImplementation(async (_nodeId: string, left: number, right: number) => ({
    left: versions[left], right: versions[right], versionCount: versions.length,
  } satisfies ChatVersionComparison));
  const setChatVersion = vi.fn();
  const store = { compareChatVersions, setChatVersion } as unknown as SceneStore;
  return { store, compareChatVersions, setChatVersion };
}

beforeEach(() => {
  FakeDiffWorker.created = 0;
  vi.stubGlobal("Worker", FakeDiffWorker);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("read-only Document View comparison dialog", () => {
  it("opens with the selected version, fetches a pair and marks changed words with semantic shapes", async () => {
    const user = userEvent.setup();
    const { store, compareChatVersions, setChatVersion } = setupStore();
    render(<OverlayProvider><Launcher store={store} /></OverlayProvider>);
    await user.click(screen.getByRole("button", { name: "Open comparison" }));

    const dialog = screen.getByRole("dialog", { name: "Document View · Compare reply versions" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    const left = within(dialog).getByRole("combobox", { name: "Left version" });
    const right = within(dialog).getByRole("combobox", { name: "Right version" });
    expect(left).toHaveValue("1");
    expect(right).toHaveValue("2");
    expect(compareChatVersions).toHaveBeenCalledWith("reply-1", 1, 2);
    await within(dialog).findByText("Word comparison ready.");
    expect(FakeDiffWorker.created).toBe(1);
    const leftPane = within(dialog).getByRole("region", { name: "Left comparison pane, version 2" });
    const rightPane = within(dialog).getByRole("region", { name: "Right comparison pane, version 3" });
    expect(leftPane).toHaveTextContent("Provider B / Model 2");
    expect(rightPane).toHaveTextContent("Provider C / Model 3");
    expect(leftPane).toHaveTextContent("2026");
    expect(rightPane).toHaveTextContent("2026");
    expect(leftPane.querySelector("del")).toHaveTextContent("blue");
    expect(rightPane.querySelector("ins")).toHaveTextContent("green");
    expect(leftPane.querySelector("del")).toHaveClass("chat-version-compare-deleted");
    expect(rightPane.querySelector("ins")).toHaveClass("chat-version-compare-inserted");
    expect(within(dialog).getByText(/Deleted:/)).toBeVisible();
    expect(within(dialog).getByText(/Inserted:/)).toBeVisible();
    expect(setChatVersion).not.toHaveBeenCalled();
  });

  it("lets a keyboard user choose ANY distinct pair, focus both panes, and close with Escape", async () => {
    const user = userEvent.setup();
    const { store, compareChatVersions, setChatVersion } = setupStore();
    render(<OverlayProvider><Launcher store={store} /></OverlayProvider>);
    const opener = screen.getByRole("button", { name: "Open comparison" });
    await user.click(opener);
    const leftSelect = screen.getByRole("combobox", { name: "Left version" });
    const rightSelect = screen.getByRole("combobox", { name: "Right version" });
    expect(within(rightSelect).getByRole("option", { name: "Version 2" })).toBeDisabled();
    await user.selectOptions(leftSelect, "0");
    await user.selectOptions(rightSelect, "1");
    await waitFor(() => expect(compareChatVersions).toHaveBeenLastCalledWith("reply-1", 0, 1));
    await screen.findByRole("region", { name: "Right comparison pane, version 2" });
    await user.click(screen.getByRole("button", { name: "Focus left pane" }));
    const leftPane = screen.getByRole("region", { name: "Left comparison pane, version 1" });
    const rightPane = screen.getByRole("region", { name: "Right comparison pane, version 2" });
    expect(document.activeElement).toBe(leftPane);
    fireEvent.keyDown(leftPane, { key: "ArrowRight", altKey: true });
    expect(document.activeElement).toBe(rightPane);
    fireEvent.keyDown(rightPane, { key: "ArrowLeft", altKey: true });
    expect(document.activeElement).toBe(leftPane);
    fireEvent.keyDown(leftPane, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
    expect(setChatVersion).not.toHaveBeenCalled();
  });

  it("styles the actual focusable reading panes with the shared visible focus token", () => {
    // jsdom cannot implement :focus-visible or a layout engine; lock the
    // CSS selector to the real tabindex=0 element, not its inert section.
    const css = readFileSync(join(REPO_SRC, "app/styles.css"), "utf-8");
    const start = css.indexOf(".chat-version-compare-text:focus-visible,");
    expect(start).toBeGreaterThanOrEqual(0);
    const rule = css.slice(start, css.indexOf("}", start));
    expect(rule).toContain("outline: 2px solid var(--gl-graph-selection)");
  });

  it("explains failed lookups instead of changing the reply or showing stale text", async () => {
    const user = userEvent.setup();
    const { store, setChatVersion } = setupStore();
    vi.mocked(store.compareChatVersions).mockRejectedValue(new Error("gone"));
    render(<OverlayProvider><Launcher store={store} /></OverlayProvider>);
    await user.click(screen.getByRole("button", { name: "Open comparison" }));
    await screen.findByText("Unable to load these chat versions.");
    expect(screen.getByRole("region", { name: "Left comparison pane, version 2" }))
      .toHaveTextContent("Comparison unavailable.");
    expect(setChatVersion).not.toHaveBeenCalled();
  });
});
