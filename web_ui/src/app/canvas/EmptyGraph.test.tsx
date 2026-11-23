import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import { EmptyGraph } from "./EmptyGraph";
import type { SceneStore } from "./sceneStore";

function OpenProbe() {
  const overlays = useOverlays();
  return <span data-testid="switcher-open">{String(overlays.isOpen("quick-switcher"))}</span>;
}

function renderEmpty() {
  const store = {
    getSpawnPoint: () => ({ x: 0, y: 0 }), addNote: vi.fn(), addTable: vi.fn(),
    loadSampleWorkspace: vi.fn(), openModelTaskSettings: vi.fn(),
  } as unknown as SceneStore;
  render(<OverlayProvider><EmptyGraph store={store} /><OpenProbe /></OverlayProvider>);
  return store;
}

describe("EmptyGraph", () => {
  it("lists real keyboard shortcuts discovered from the shortcut resolver", () => {
    renderEmpty();
    const hints = screen.getByRole("list", { name: "Keyboard shortcuts" });
    expect(hints).toHaveTextContent("Command palette");
    expect(hints).toHaveTextContent("Recent chats");
    expect(hints).toHaveTextContent("Chat library");
    expect(screen.getByRole("group", { name: "Ctrl+K" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Ctrl+P" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Ctrl+L" })).toBeInTheDocument();
  });

  it("opens the quick switcher's recent chats from the empty canvas", async () => {
    const user = userEvent.setup();
    renderEmpty();
    expect(screen.getByTestId("switcher-open")).toHaveTextContent("false");
    await user.click(screen.getByRole("button", { name: "Open a recent chat…" }));
    expect(screen.getByTestId("switcher-open")).toHaveTextContent("true");
  });

  it("still offers the sample workspace", async () => {
    const user = userEvent.setup();
    const store = renderEmpty();
    await user.click(screen.getByRole("button", { name: "Open sample workspace" }));
    expect(store.loadSampleWorkspace).toHaveBeenCalledOnce();
  });
});
