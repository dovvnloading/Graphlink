/** Run inputs survive navigation as a session draft (#1153). */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkflowSpec } from "../../lib/workflow-contract/generated/workflow";
import { WorkflowRunLauncher } from "./WorkflowRunLauncher";
import { hasWorkflowDraft } from "./workflowDrafts";

const spec = {
  workflow_id: "wf", revision: "rev-1", name: "Summarize", steps: [],
  inputs: [{ name: "topic", kind: "text", required: true }], outputs: [],
} as unknown as WorkflowSpec;

function respond(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: async () => body } as Response);
}

afterEach(() => vi.unstubAllGlobals());

describe("WorkflowRunLauncher drafts (#1153)", () => {
  it("restores unsaved inputs after the launcher unmounts, and Discard draft resets them", async () => {
    const user = userEvent.setup();
    const first = render(<WorkflowRunLauncher spec={spec} workspace={1} draftKey="run:rev-1" onStarted={() => {}} />);
    await user.type(screen.getByRole("textbox", { name: "Topic" }), "solar");
    first.unmount();

    render(<WorkflowRunLauncher spec={spec} workspace={1} draftKey="run:rev-1" onStarted={() => {}} />);
    expect(screen.getByRole("textbox", { name: "Topic" })).toHaveValue("solar");
    expect(screen.getByText("Your unsaved inputs were kept.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Discard draft" }));
    expect(screen.getByRole("textbox", { name: "Topic" })).toHaveValue("");
    expect(hasWorkflowDraft("run:rev-1")).toBe(false);
  });

  it("a workspace change keeps the inputs but needs a fresh preview", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(() => respond({ fingerprint: "fp", requires_approval: false, steps: [] })));
    const { rerender } = render(<WorkflowRunLauncher spec={spec} workspace={1} draftKey="run:rev-1" onStarted={() => {}} />);
    await user.type(screen.getByRole("textbox", { name: "Topic" }), "solar");
    await user.click(screen.getByRole("button", { name: "Preview workflow" }));
    expect(await screen.findByRole("button", { name: "Start reviewed workflow" })).toBeInTheDocument();

    rerender(<WorkflowRunLauncher spec={spec} workspace={2} draftKey="run:rev-1" onStarted={() => {}} />);
    expect(screen.getByRole("textbox", { name: "Topic" })).toHaveValue("solar");
    expect(screen.queryByRole("button", { name: "Start reviewed workflow" })).toBeNull();
    expect(screen.getByRole("button", { name: "Preview workflow" })).toBeInTheDocument();
  });

  it("a successful launch clears the draft", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn((path: string) => respond(path.endsWith("/preview")
      ? { fingerprint: "fp", requires_approval: false, steps: [] }
      : path.endsWith("/approve") ? { approval_token: "token" } : { run_id: "run-9" }));
    vi.stubGlobal("fetch", fetchMock);
    const onStarted = vi.fn();
    render(<WorkflowRunLauncher spec={spec} workspace={1} draftKey="run:rev-1" onStarted={onStarted} />);
    await user.type(screen.getByRole("textbox", { name: "Topic" }), "solar");
    await user.click(screen.getByRole("button", { name: "Preview workflow" }));
    await user.click(await screen.findByRole("button", { name: "Start reviewed workflow" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("run-9"));
    expect(hasWorkflowDraft("run:rev-1")).toBe(false);
  });
});
