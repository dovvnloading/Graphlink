/** The workflow node editor follows authoritative configuration (#1154). */
import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { OverlayProvider } from "../overlays/overlays";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { SceneStore } from "./sceneStore";
import { WorkflowNodeView, type WorkflowFlowNode } from "./WorkflowNodeView";
import type { WsTransport } from "../../lib/ws/transport";

function state(topic: string) {
  return JSON.stringify({
    document: {
      format: "graphlink-workflow", parameters: [],
      spec: { workflow_id: "wf", revision: "rev-1", name: "Summarize", steps: [],
        inputs: [{ name: "topic", kind: "text" }], outputs: [] },
    },
    version: 1, inputs: { topic }, sources: {}, parameters: {}, outputs: {}, lastInputHash: "", childRunId: "",
  });
}

function setup(initial = "solar") {
  const request = vi.fn<WsTransport["request"]>().mockImplementation(async (_topic, intent) =>
    intent === "workflowLibrary" ? [] : true);
  const store = new SceneStore({ request, fireIntent: vi.fn(), subscribe: vi.fn(() => () => {}) } as unknown as WsTransport);
  const view = (raw: string) => (
    <OverlayProvider>
      <ReactFlowProvider>
        <WorkflowNodeView {...({ id: "wf-node", selected: false, positionAbsoluteX: 0, positionAbsoluteY: 0,
          data: { title: "Summarize", raw, isCollapsed: false, store } } as unknown as NodeProps<WorkflowFlowNode>)} />
        <ConfirmDialog />
      </ReactFlowProvider>
    </OverlayProvider>
  );
  const utils = render(view(state(initial)));
  return { request, push: (topic: string) => utils.rerender(view(state(topic))) };
}

const field = () => screen.getByRole("textbox", { name: "Topic" });
const configureCalls = (request: ReturnType<typeof setup>["request"]) =>
  request.mock.calls.filter(([, intent, args]) => intent === "mutateWorkflowNode" && (args as unknown[])[0] === "configure");

describe("WorkflowNodeView configuration freshness (#1154)", () => {
  it("a clean form follows an authoritative change such as undo", () => {
    const { push } = setup("solar");
    expect(field()).toHaveValue("solar");
    push("wind");
    expect(field()).toHaveValue("wind");
    expect(screen.queryByText("Inputs changed")).toBeNull();
  });

  it("a dirty form keeps its edits and asks before saving over newer values", async () => {
    const user = userEvent.setup();
    const { push, request } = setup("solar");
    fireEvent.change(field(), { target: { value: "my edit" } });
    push("wind");
    expect(field()).toHaveValue("my edit");
    expect(screen.getByText("Inputs changed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save inputs" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Preview run" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Keep my edits" }));
    await user.click(screen.getByRole("button", { name: "Save inputs" }));
    await waitFor(() => expect(configureCalls(request)).toHaveLength(1));
    expect((configureCalls(request)[0][2] as unknown[])[1]).toMatchObject({
      inputs: { topic: "my edit" }, expected_configuration: { inputs: { topic: "wind" } },
    });
  });

  it("Reload inputs discards the edits for the current values", async () => {
    const user = userEvent.setup();
    const { push, request } = setup("solar");
    fireEvent.change(field(), { target: { value: "my edit" } });
    push("wind");
    await user.click(screen.getByRole("button", { name: "Reload inputs" }));
    expect(field()).toHaveValue("wind");
    expect(screen.queryByText("Inputs changed")).toBeNull();
    expect(configureCalls(request)).toHaveLength(0);
  });

  it("an uncontested save sends the baseline it was edited from", async () => {
    const user = userEvent.setup();
    const { request } = setup("solar");
    fireEvent.change(field(), { target: { value: "solar power" } });
    await user.click(screen.getByRole("button", { name: "Save inputs" }));
    await waitFor(() => expect(configureCalls(request)).toHaveLength(1));
    expect((configureCalls(request)[0][2] as unknown[])[1]).toMatchObject({
      inputs: { topic: "solar power" }, expected_configuration: { inputs: { topic: "solar" } },
    });
  });
});
