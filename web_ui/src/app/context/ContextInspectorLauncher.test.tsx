import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SceneStore } from "../canvas/sceneStore";
import type { ComposerStore } from "../chrome/composerStore";
import type { WsTransport } from "../../lib/ws/transport";
import { ContextInspectorLauncher } from "./ContextInspectorLauncher";

function fixture(options: { noChat?: boolean; selected?: string | null; target?: string | null } = {}) {
  const nodes = options.noChat ? [] : [
    { id: "n1", kind: "chat" },
    { id: "n2", kind: "chat" },
  ];
  const snapshot = { nodes };
  const sceneStore = {
    subscribe: (_listener: () => void) => () => {},
    getScene: () => snapshot,
    getSelectedNodeId: () => options.selected ?? null,
    getReplyTargetNodeId: () => options.target ?? null,
  } as unknown as SceneStore;
  const composer = { draft: { text: "draft one" } };
  const composerStore = { getComposer: () => composer } as unknown as ComposerStore;
  const request = vi.fn().mockResolvedValue({
    nodeId: options.target ?? options.selected ?? "n2",
    provider: "ollama",
    model: "local-model",
    contextWindow: 8192,
    promptBudget: 6144,
    outputReserve: 2048,
    promptTokens: 100,
    leavesDevice: false,
    sources: [
      { sourceId: "system:chat", origin: "system:settings", trust: "system",
        kind: "instructions", tokenCount: 20, sentTokenCount: 20,
        included: true, truncated: false, leavesDevice: false, reason: "mandatory" },
      { sourceId: "knowledge:large", origin: "document:large", trust: "retrieved",
        kind: "knowledge_hit", tokenCount: 20000, sentTokenCount: 0,
        included: false, truncated: false, leavesDevice: false, reason: "exceeds prompt budget" },
    ],
  });
  const transport = { request } as unknown as WsTransport;
  return { sceneStore, composerStore, composer, transport, request };
}

describe("Context Inspector", () => {
  it("does no fetch until opened and shows included/excluded sources and egress", async () => {
    const f = fixture({ target: "n1", selected: "n2" });
    render(<ContextInspectorLauncher sceneStore={f.sceneStore} composerStore={f.composerStore} transport={f.transport} />);
    expect(f.request).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Context Inspector" }));
    await waitFor(() => expect(f.request).toHaveBeenCalledWith("scene", "inspectChatContext", ["n1", "draft one"]));
    expect(await screen.findByText(/100 \/ 6144 prompt tokens/)).toBeInTheDocument();
    expect(screen.getByText(/Local model route/)).toBeInTheDocument();
    expect(screen.getByText(/exceeds prompt budget/)).toBeInTheDocument();
    expect(screen.getByText(/system:settings/)).toBeInTheDocument();
    expect(screen.getByText(/document:large/)).toBeInTheDocument();
    expect(screen.queryByText(/20000 tokens/)).toBeInTheDocument();
  });

  it("refreshes using a new draft and closes without mutating the scene", async () => {
    const f = fixture({ selected: "n1" });
    render(<ContextInspectorLauncher sceneStore={f.sceneStore} composerStore={f.composerStore} transport={f.transport} />);
    fireEvent.click(screen.getByRole("button", { name: "Context Inspector" }));
    await waitFor(() => expect(f.request).toHaveBeenCalledTimes(1));
    f.composer.draft.text = "draft two";
    fireEvent.click(screen.getByRole("button", { name: "Refresh context" }));
    await waitFor(() => expect(f.request).toHaveBeenCalledWith("scene", "inspectChatContext", ["n1", "draft two"]));
    fireEvent.click(screen.getByRole("button", { name: "Close context inspector" }));
    expect(screen.queryByRole("region", { name: "Next chat context" })).not.toBeInTheDocument();
  });

  it("disables itself when no chat exists", () => {
    const f = fixture({ noChat: true });
    render(<ContextInspectorLauncher sceneStore={f.sceneStore} composerStore={f.composerStore} transport={f.transport} />);
    expect(screen.getByRole("button", { name: "Context Inspector" })).toBeDisabled();
    expect(f.request).not.toHaveBeenCalled();
  });

  it("displays a request failure without fabricating source rows", async () => {
    const f = fixture();
    f.request.mockRejectedValue(new Error("offline"));
    render(<ContextInspectorLauncher sceneStore={f.sceneStore} composerStore={f.composerStore} transport={f.transport} />);
    fireEvent.click(screen.getByRole("button", { name: "Context Inspector" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("offline");
    expect(screen.queryByText(/prompt tokens/)).not.toBeInTheDocument();
  });
});
