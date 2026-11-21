import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChatRerunDialog } from "./ChatRerunDialog";
import type { ChatRerunPreview, SceneStore } from "./sceneStore";
import { OverlayProvider, useOverlays } from "../overlays/overlays";

function makePreview(count: number): ChatRerunPreview {
  return {
    previewToken: "preview-token",
    count,
    requiresConfirmation: count > 10,
    skipped: [],
    replies: Array.from({ length: count }, (_, index) => ({
      nodeId: "reply-" + index,
      title: "Reply " + (index + 1),
      provider: "Provider",
      model: "Model",
      estimatedInputTokens: 100 + index,
    })),
  };
}

function renderDialog(
  count: number,
  skipped: ChatRerunPreview["skipped"] = [],
  status: "running" | "completed" = "running",
) {
  const previewChatRerun = vi.fn(async () => ({ ...makePreview(count), skipped }));
  const onRunBoundNodes = vi.fn();
  const startChatRerun = vi.fn(async () => ({ jobId: "job-1" }));
  const getChatRerunStatus = vi.fn(async () => ({
    jobId: "job-1", status,
    completed: 1, total: count, currentNodeId: "reply-1",
    error: "", cancelRequested: false,
  }));
  const cancelChatRerun = vi.fn(async () => ({ ok: true }));
  const executeGraphRun = vi.fn();
  const store = {
    previewChatRerun, startChatRerun, getChatRerunStatus, cancelChatRerun, executeGraphRun,
  } as unknown as SceneStore;

  function Harness() {
    const overlays = useOverlays();
    return (
      <>
        <button type="button" onClick={() => overlays.open("chat-rerun", "dialog")}>Open preview</button>
        <ChatRerunDialog store={store} nodeId="edited-user" onRunBoundNodes={onRunBoundNodes} />
      </>
    );
  }

  render(<OverlayProvider><Harness /></OverlayProvider>);
  return { previewChatRerun, startChatRerun, getChatRerunStatus, cancelChatRerun, onRunBoundNodes, executeGraphRun };
}

describe("ChatRerunDialog", () => {
  it("previews ordered replies with model, input tokens and keyboard-operable scope", async () => {
    const user = userEvent.setup();
    const spies = renderDialog(2);
    await user.click(screen.getByRole("button", { name: "Open preview" }));
    expect(screen.getByRole("dialog", { name: "Re-run from here" })).toBeInTheDocument();
    await screen.findByText("Reply 1");
    expect(screen.getByRole("list", { name: /2 replies in parent-before-child order/ }))
      .toBeInTheDocument();
    expect(screen.getByText(/Estimated input: 100 tokens/)).toBeInTheDocument();
    expect(screen.getAllByText(/Provider \/ Model/)).toHaveLength(2);
    const branches = screen.getByRole("radio", { name: "All branches below" });
    branches.focus();
    await user.keyboard(" ");
    await waitFor(() => {
      expect(spies.previewChatRerun).toHaveBeenCalledWith("edited-user", "all_branches_below");
    });
    await screen.findByText("Reply 2");
    await user.click(screen.getByRole("button", { name: "Re-run replies" }));
    expect(spies.startChatRerun).toHaveBeenCalledWith(
      "edited-user", "all_branches_below", "preview-token", false,
    );
  });

  it("requires an explicit confirmation for 11 replies, not for 10", async () => {
    const user = userEvent.setup();
    const spies = renderDialog(11);
    await user.click(screen.getByRole("button", { name: "Open preview" }));
    await screen.findByText("Reply 11");
    expect(screen.getByRole("button", { name: "Re-run replies" })).toBeDisabled();
    const consent = screen.getByRole("checkbox", { name: /I confirm re-running more than 10 replies/ });
    await user.click(consent);
    expect(screen.getByRole("button", { name: "Re-run replies" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Re-run replies" }));
    expect(spies.startChatRerun).toHaveBeenCalledWith(
      "edited-user", "this_path", "preview-token", true,
    );
  });

  it("announces batch progress, then cancels cooperatively", async () => {
    const user = userEvent.setup();
    const spies = renderDialog(3);
    await user.click(screen.getByRole("button", { name: "Open preview" }));
    await screen.findByText("Reply 3");
    await user.click(screen.getByRole("button", { name: "Re-run replies" }));
    expect(await screen.findByRole("progressbar", { name: "Replies completed" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("status", { name: "" }).textContent)
      .toContain("1 of 3 replies completed"));
    await user.click(screen.getByRole("button", { name: "Cancel re-run" }));
    expect(spies.cancelChatRerun).toHaveBeenCalledWith("job-1");
    expect(screen.getByRole("button", { name: "Cancel re-run" })).toBeDisabled();
  });

  it("lists side effects as skipped, and hands bound outputs to Graph Runner without execution", async () => {
    const user = userEvent.setup();
    const skipped: ChatRerunPreview["skipped"] = [
      { nodeId: "agent", title: "Workspace agent", kind: "harness", category: "manual",
        reason: "Workspace agent nodes are never re-run automatically; re-run manually." },
      { nodeId: "builder", title: "Builder plan", kind: "plan", category: "manual",
        reason: "Builder nodes are never re-run automatically; re-run manually." },
      { nodeId: "sandbox", title: "Virtual Environment Runner", kind: "code_sandbox", category: "manual",
        reason: "Code sandbox nodes are never re-run automatically; re-run manually." },
      { nodeId: "plugin", title: "Plugin action", kind: "example.action", category: "manual",
        reason: "Plugin nodes are never re-run automatically; re-run manually." },
      { nodeId: "tool", title: "Tool call reply", kind: "chat", category: "manual",
        reason: "This reply executed tools; re-run it manually." },
      { nodeId: "model", title: "Local model", kind: "model", category: "bound",
        reason: "Data-bound output requires Graph Runner preview and approval." },
      { nodeId: "report", title: "Data report", kind: "note", category: "bound",
        reason: "Data-bound output requires Graph Runner preview and approval." },
    ];
    const spies = renderDialog(0, skipped);
    await user.click(screen.getByRole("button", { name: "Open preview" }));
    const skippedRegion = await screen.findByRole("region", { name: "Skipped nodes" });
    expect(skippedRegion).toHaveTextContent("never executed by this re-run");
    expect(skippedRegion).toHaveTextContent("This reply executed tools");
    expect(skippedRegion).toHaveTextContent("Graph Runner preview and approval");
    expect(screen.getByRole("button", { name: "Re-run replies" })).toBeDisabled();
    const action = screen.getByRole("button", { name: "Run bound nodes" });
    action.focus();
    await user.keyboard(" ");
    expect(spies.onRunBoundNodes).toHaveBeenCalledOnce();
    expect(spies.onRunBoundNodes).toHaveBeenCalledWith(["model", "report"]);
    expect(spies.startChatRerun).not.toHaveBeenCalled();
    expect(spies.executeGraphRun).not.toHaveBeenCalled();
  });

  it("uses the shared Dialog focus restoration and Escape behaviour", async () => {
    const user = userEvent.setup();
    renderDialog(1);
    const trigger = screen.getByRole("button", { name: "Open preview" });
    trigger.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("dialog", { name: "Re-run from here" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Re-run from here" })).toBeNull());
    expect(trigger).toHaveFocus();
  });
  it("fetches a fresh preview grant after completing a batch", async () => {
    const user = userEvent.setup();
    const spies = renderDialog(1, [], "completed");
    spies.previewChatRerun
      .mockResolvedValueOnce(makePreview(1))
      .mockResolvedValueOnce({ ...makePreview(1), previewToken: "second-grant" });
    await user.click(screen.getByRole("button", { name: "Open preview" }));
    await screen.findByText("Reply 1");
    await user.click(screen.getByRole("button", { name: "Re-run replies" }));
    await screen.findByRole("button", { name: "Preview another re-run" });
    await user.click(screen.getByRole("button", { name: "Preview another re-run" }));
    await waitFor(() => expect(spies.previewChatRerun).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByRole("button", { name: "Re-run replies" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Re-run replies" }));
    expect(spies.startChatRerun).toHaveBeenLastCalledWith(
      "edited-user", "this_path", "second-grant", false,
    );
  });

  it("refreshes a rejected preview grant instead of trapping the retry action", async () => {
    const user = userEvent.setup();
    const spies = renderDialog(1);
    spies.startChatRerun.mockRejectedValueOnce(new Error("The plan changed."));
    spies.previewChatRerun
      .mockResolvedValueOnce(makePreview(1))
      .mockResolvedValueOnce({ ...makePreview(1), previewToken: "refreshed-grant" });
    await user.click(screen.getByRole("button", { name: "Open preview" }));
    await screen.findByText("Reply 1");
    await user.click(screen.getByRole("button", { name: "Re-run replies" }));
    await screen.findByText("The plan changed.");
    await waitFor(() => expect(spies.previewChatRerun).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByRole("button", { name: "Re-run replies" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Re-run replies" }));
    expect(spies.startChatRerun).toHaveBeenLastCalledWith(
      "edited-user", "this_path", "refreshed-grant", false,
    );
  });

});
