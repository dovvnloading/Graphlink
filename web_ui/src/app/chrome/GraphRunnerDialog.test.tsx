import { ReactFlowProvider } from "@xyflow/react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { makeRequestOnlyTransport } from "../../lib/ws/transport.testUtils";
import { SceneStore, type GraphRun } from "../canvas/sceneStore";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import { GraphRunnerDialog } from "./GraphRunnerDialog";

function OpenRunner() {
  const overlays = useOverlays();
  return <button type="button" onClick={() => overlays.open("graph-runner", "dialog")}>Open runner</button>;
}

const step = {
  label: "Hours by team", nodeId: "chart", sourceId: "table", kind: "chart",
  operation: "group_sum", status: "ready", detail: "Input has changed.",
};
const run: GraphRun = {
  runId: "latest-run", startedAt: "2026-11-08T12:00:00Z", status: "completed",
  steps: [{ ...step, status: "completed", detail: "Output updated." }],
};

function setup(selectedIds: string[] = [], sceneNodes: Array<Record<string, unknown>> = []) {
  const user = userEvent.setup();
  const { transport, request } = makeRequestOnlyTransport();
  const store = new SceneStore(transport);
  if (sceneNodes.length) Object.assign(store.getScene(), { nodes: sceneNodes });
  const nodes = selectedIds.map((id) => ({ id, selected: true, position: { x: 0, y: 0 }, data: {} }));
  render(<OverlayProvider><ReactFlowProvider initialNodes={nodes}>
    <OpenRunner /><GraphRunnerDialog store={store} />
  </ReactFlowProvider></OverlayProvider>);
  return { user, request, store };
}

describe("Graph Runner", () => {
  it("formats scientific summaries in Graph Runner without changing checkpointed results", async () => {
    const summary = "Active-site turnover kcat=1200 1/min; kcat/Km=1e+06 M⁻¹ s⁻¹.";
    const scientific: GraphRun = { ...run, steps: [{
      ...run.steps[0], kind: "scientific", operation: "catalytic_constants",
      scientificResult: { operationId: "catalytic_constants", summary, inputs: {},
        values: { kcat_over_km_si: 1000000 }, provenance: { resultHash: "a".repeat(64) } },
    }] };
    const { user, request } = setup();
    request.mockResolvedValueOnce(scientific);
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    const latest = await screen.findByRole("region", { name: "Latest run" });
    expect(latest).toHaveTextContent("kcat/Km=1,000,000 M⁻¹ s⁻¹");
    expect(scientific.steps[0].scientificResult?.summary).toBe(summary);
  });

  it("previews the selected nodes and discards the preview when the scope changes", async () => {
    const { user, request } = setup(["source", "other"]);
    request.mockResolvedValueOnce(null)
      .mockResolvedValueOnce({ previewToken: "selection", steps: [step] })
      .mockResolvedValueOnce({ previewToken: "all", steps: [] });
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    const scope = screen.getByRole("combobox", { name: "Run scope" });
    expect(scope).toHaveValue("selection");
    const preview = screen.getByRole("button", { name: "Preview run" });
    await user.click(preview);
    expect(request).toHaveBeenLastCalledWith("scene", "previewGraphRun", [["source", "other"]]);
    await user.selectOptions(scope, "all");
    expect(screen.queryByRole("button", { name: "Run graph" })).toBeNull();
    await user.click(preview);
    expect(request).toHaveBeenLastCalledWith("scene", "previewGraphRun", [[]]);
    expect(await screen.findByText(/No connected outputs/)).toBeVisible();
  });

  it("accepts a bound-node handoff without executing anything before separate approval", async () => {
    const { user, request, store } = setup([], [
      { id: "bound-model", kind: "model", title: "Bound local model" },
    ]);
    request.mockResolvedValueOnce(null)
      .mockResolvedValueOnce({ previewToken: "review-only", steps: [step] });
    store.requestGraphRunnerNodes(["bound-model", "bound-model"]);
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    expect(await screen.findByRole("combobox", { name: "Run scope" })).toHaveValue("selection");
    await user.click(screen.getByRole("button", { name: "Preview run" }));
    expect(request).toHaveBeenCalledWith("scene", "previewGraphRun", [["bound-model"]]);
    expect(await screen.findByRole("region", { name: "Run preview" })).toBeInTheDocument();
    expect(request.mock.calls.filter(([, intent]) =>
      intent === "executeGraphRun" || intent === "executeGraphModel")).toHaveLength(0);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(store.getGraphRunnerRequestedIds()).toBeNull());
  });

  it("routes a saved Model-to-Note handoff into the existing model preview, not execution", async () => {
    const { user, request, store } = setup([], [
      { id: "source", kind: "chat", title: "Model source" },
      { id: "note", kind: "note", title: "Local model output" },
    ]);
    const saved = [{
      sourceId: "source", targetId: "note", nodeKind: "note",
      provider: "Ollama", modelId: "qwen3:8b",
      inputType: "text", outputType: "text", version: 1, status: "stale",
    }];
    request.mockResolvedValueOnce(null)
      .mockResolvedValueOnce(saved)
      .mockResolvedValueOnce({ previewToken: "review-model", steps: [{
        ...step, nodeId: "note", sourceId: "source", kind: "model", operation: "model",
      }] });
    store.requestGraphRunnerNodes(["note"]);
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    const modelInput = await screen.findByRole("textbox", { name: "Installed Ollama model ID" });
    await waitFor(() => expect(modelInput).toHaveValue("qwen3:8b"));
    expect(screen.getByText(/a separate approval is required/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Preview model step" }));
    expect(request).toHaveBeenCalledWith(
      "scene", "previewGraphModel", ["source", "note", "Ollama", "qwen3:8b"],
    );
    expect(request.mock.calls.filter(([, intent]) =>
      intent === "executeGraphRun" || intent === "executeGraphModel")).toHaveLength(0);
  });

  it("retries a reviewed preview with the same operation ID after a lost reply", async () => {
    const { user, request } = setup();
    request.mockResolvedValueOnce(null)
      .mockResolvedValueOnce({ previewToken: "preview", steps: [step] })
      .mockRejectedValueOnce(new Error("Reply timed out."))
      .mockResolvedValueOnce(run).mockResolvedValueOnce(run);
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    await user.click(screen.getByRole("button", { name: "Preview run" }));
    const preview = await screen.findByRole("region", { name: "Run preview" });
    expect(within(preview).getByText("Ready")).toBeVisible();
    expect(request.mock.calls).toEqual([["scene", "getGraphRun", []], ["scene", "previewGraphRun", [[]]]]);
    const execute = within(preview).getByRole("button", { name: "Run graph" });
    await user.click(execute);
    expect(await screen.findByRole("alert")).toHaveTextContent("Reply timed out.");
    await user.click(execute);
    expect(await screen.findByRole("region", { name: "Latest run" })).toHaveTextContent("Output updated.");
    const attempts = request.mock.calls.filter(([, intent]) => intent === "executeGraphRun");
    expect(attempts).toHaveLength(2);
    expect(attempts[0]).toEqual(["scene", "executeGraphRun", [[], "preview", expect.any(String)]]);
    expect(attempts[1]).toEqual(attempts[0]);
    expect(screen.queryByRole("region", { name: "Run preview" })).toBeNull();
  });

  it("resumes an interrupted earlier run and keeps the newest run first", async () => {
    const { user, request } = setup();
    const older = { ...run, runId: "older-run", startedAt: "2026-11-06T11:00:00Z", status: "interrupted" };
    request.mockResolvedValueOnce({ ...run, history: [older] })
      .mockResolvedValueOnce({ ...older, status: "completed" })
      .mockResolvedValueOnce({ ...run, history: [{ ...older, status: "completed" }] });
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    await user.click(await screen.findByText("Earlier runs (1)"));
    await user.click(screen.getByText(/ · Interrupted$/));
    await user.click(screen.getByRole("button", { name: "Resume run" }));
    expect(request).toHaveBeenCalledWith("scene", "resumeGraphRun", ["older-run"]);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Resume run" })).toBeNull());
    expect(within(screen.getByRole("region", { name: "Latest run" })).getByRole("status")).toHaveTextContent("Completed");
    expect(screen.getByText(/ · Completed$/)).toBeInTheDocument();
  });

  it("shows an uncertain provider request without offering automatic replay", async () => {
    const { user, request } = setup();
    const uncertain: GraphRun = {
      ...run, status: "reconciliation-required", steps: [{
        ...step, status: "reconciliation-required",
        detail: "The external request may already have run.",
        externalAttempt: {
          requestId: "graph-1234567890abcdef1234567890abcdef",
          kind: "model", provider: "profile/openai", model: "example-model",
          tool: "", inputRef: "artifact/version-1", inputHash: "a".repeat(64),
          phase: "dispatched",
        },
      }],
    };
    request.mockResolvedValueOnce(uncertain);
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    const latest = await screen.findByRole("region", { name: "Latest run" });
    expect(within(latest).getAllByText("Reconciliation required")).toHaveLength(2);
    expect(within(latest).getByRole("alert")).toHaveTextContent("will not retry");
    expect(within(latest).queryByRole("button", { name: "Resume run" })).toBeNull();
    await user.click(within(latest).getByRole("button", { name: "External request" }));
    expect(latest).toHaveTextContent("graph-1234567890abcdef1234567890abcdef");
    expect(latest).toHaveTextContent("profile/openai");
    expect(latest).toHaveTextContent("Check the provider");
  });

  it("can continue independent safe work without retrying a remote request", async () => {
    const { user, request } = setup();
    const pending = { ...step, nodeId: "local", sourceId: "source", status: "pending" };
    const first: GraphRun = {
      ...run, status: "reconciliation-required",
      steps: [{ ...step, status: "reconciliation-required" }, pending],
    };
    const finished: GraphRun = { ...first, steps: [first.steps[0], { ...pending, status: "completed" }] };
    request.mockResolvedValueOnce(first).mockResolvedValueOnce(finished).mockResolvedValueOnce(finished);
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    const button = await screen.findByRole("button", { name: "Continue safe steps" });
    expect(screen.getByText(/will not be repeated/)).toBeInTheDocument();
    await user.click(button);
    expect(request).toHaveBeenCalledWith("scene", "resumeGraphRun", ["latest-run"]);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Continue safe steps" })).toBeNull());
  });

  it("previews and runs a specifically chosen local model into an unbound note", async () => {
    const { user, request } = setup([], [
      { id: "source", kind: "chat", title: "Source chat" },
      { id: "destination", kind: "note", title: "Reply note" },
    ]);
    const modelStep = {
      ...step, kind: "model", operation: "model", nodeId: "destination",
      sourceId: "source", label: "Reply note", detail: "Local Ollama model: qwen3:8b",
    };
    request.mockResolvedValueOnce(null)
      .mockResolvedValueOnce([]) // Opening the model panel reads saved bindings.
      .mockResolvedValueOnce({ previewToken: "model-preview", steps: [modelStep] })
      .mockResolvedValueOnce({
        ...run, steps: [{ ...modelStep, status: "completed", detail: "External output checkpointed." }],
      })
      .mockResolvedValueOnce({
        ...run, steps: [{ ...modelStep, status: "completed", detail: "External output checkpointed." }],
      })
      .mockResolvedValueOnce([]); // Refresh saved provenance after execution.
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    await user.click(screen.getByRole("button", { name: "Run a local model into a note" }));
    await user.type(screen.getByRole("textbox", { name: "Installed Ollama model ID" }), "qwen3:8b");
    await user.click(screen.getByRole("button", { name: "Preview model step" }));
    expect(request).toHaveBeenCalledWith(
      "scene", "previewGraphModel", ["source", "destination", "Ollama", "qwen3:8b"],
    );
    const modelPreview = await screen.findByRole("region", { name: "Model preview" });
    expect(modelPreview).toHaveTextContent("Local Ollama model: qwen3:8b");
    await user.click(within(modelPreview).getByRole("button", { name: "Run local model" }));
    expect(request).toHaveBeenCalledWith(
      "scene", "executeGraphModel", [
        "source", "destination", "Ollama", "qwen3:8b", "model-preview", expect.any(String),
      ],
    );
    expect(await screen.findByRole("region", { name: "Latest run" })).toHaveTextContent("External output checkpointed.");
  });

  it("saves, reselects and disconnects a typed model connection without an implicit request", async () => {
    const { user, request } = setup([], [
      { id: "source", kind: "chat", title: "Text source" },
      { id: "destination", kind: "note", title: "Model output" },
    ]);
    const saved = [{
      sourceId: "source", targetId: "destination", provider: "Ollama", modelId: "qwen3:8b",
      inputType: "text", outputType: "text", version: 1, status: "unrun",
    }];
    request.mockResolvedValueOnce(null)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce(saved)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce(saved);
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    const toggle = screen.getByRole("button", { name: "Run a local model into a note" });
    await user.click(toggle);
    await user.type(screen.getByRole("textbox", { name: "Installed Ollama model ID" }), "qwen3:8b");
    await user.click(screen.getByRole("button", { name: "Save model connection" }));
    expect(request).toHaveBeenCalledWith("scene", "bindGraphModel", ["source", "destination", "Ollama", "qwen3:8b"]);
    expect(await screen.findByText(/Saved text → text connection/)).toBeVisible();
    expect(request.mock.calls.filter(([, intent]) => intent === "executeGraphModel")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Disconnect model connection" }));
    expect(request).toHaveBeenCalledWith("scene", "unbindGraphModel", ["destination"]);
    await user.click(toggle); // close
    await user.click(toggle); // saved connections are loaded fresh when reopened
    expect(request).toHaveBeenCalledWith("scene", "getGraphModelBindings", []);
    expect(await screen.findByRole("combobox", { name: "Saved model connections" })).toHaveValue("destination");
    expect(screen.getByRole("textbox", { name: "Installed Ollama model ID" })).toHaveValue("qwen3:8b");
  });

  it("creates an executable Model node and previews downstream steps as waiting", async () => {
    const { user, request } = setup([], [
      { id: "source", kind: "chat", title: "Initial input" },
      { id: "destination", kind: "note", title: "Output note" },
    ]);
    const modelRows = [{
      nodeKind: "model", sourceId: "source", targetId: "model-1",
      provider: "Ollama", modelId: "qwen3:8b", version: 1,
      inputType: "text", outputType: "text", status: "unrun",
    }];
    request.mockResolvedValueOnce(null)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce("model-1")
      .mockResolvedValueOnce(modelRows)
      .mockResolvedValueOnce({
        previewToken: "multi-model-plan", steps: [
          { ...step, kind: "model", operation: "model", nodeId: "model-1", sourceId: "source" },
          { ...step, kind: "note", nodeId: "destination", sourceId: "model-1",
            status: "waiting", detail: "Awaiting an upstream Model result." },
        ],
      });
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    await user.click(screen.getByRole("button", { name: "Run a local model into a note" }));
    await user.type(screen.getByRole("textbox", { name: "Installed Ollama model ID" }), "qwen3:8b");
    await user.click(screen.getByRole("button", { name: "Create Model node" }));
    expect(request).toHaveBeenCalledWith("scene", "addModelNode", ["source", "Ollama", "qwen3:8b"]);
    expect(await screen.findByRole("region", { name: "Model nodes in graph" })).toHaveTextContent("qwen3:8b");
    expect(request.mock.calls.some(([, action]) => action === "executeGraphRun")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Preview run" }));
    const preview = await screen.findByRole("region", { name: "Run preview" });
    expect(preview).toHaveTextContent("Waiting on upstream");
    expect(within(preview).getByRole("button", { name: "Run graph" })).toBeEnabled();
    expect(request).toHaveBeenCalledWith("scene", "previewGraphRun", [[]]);
  });


  it("shows science fan-in receipts without calculating from preview", async () => {
    const { user, request } = setup([], [
      { id: "alpha", title: "Source A", kind: "science_core.workbench" },
      { id: "beta", title: "Source B", kind: "science_core.workbench" },
      { id: "fitted", title: "Fit", kind: "science_statistics.workbench" },
    ]);
    request.mockResolvedValueOnce(null).mockResolvedValueOnce({
      previewToken: "two-sources", steps: [{
        ...step, nodeId: "fitted", kind: "scientific", operation: "linear_regression",
        sourceId: "alpha", sourceIds: ["alpha", "beta"], status: "waiting",
        detail: "Awaiting an upstream result.",
      }],
    });
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    await user.click(screen.getByRole("button", { name: "Preview run" }));
    const preview = await screen.findByRole("region", { name: "Run preview" });
    expect(preview).toHaveTextContent("Source A, Source B");
    expect(preview).toHaveTextContent("Waiting on upstream");
    expect(request.mock.calls.filter(([, name]) => name === "executeGraphRun")).toHaveLength(0);
  });

  it("can cancel a scientific run resumed from history instead of only the newest run", async () => {
    const { user, request } = setup();
    const older: GraphRun = {
      ...run, runId: "older-science-run", startedAt: "2026-11-06T11:00:00Z",
      status: "running", steps: [{
        ...step, nodeId: "science", sourceId: "", sourceIds: [], kind: "scientific",
        operation: "convert_unit", status: "pending",
      }],
    };
    request.mockResolvedValueOnce({ ...run, history: [older] })
      .mockResolvedValueOnce({ ok: true })
      .mockResolvedValueOnce({ ...run, history: [{ ...older, status: "cancelled" }] });
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    await user.click(await screen.findByText("Earlier runs (1)"));
    await user.click(screen.getByText(/ · Running$/));
    await user.click(screen.getByRole("button", { name: "Cancel run" }));
    expect(request).toHaveBeenCalledWith("scene", "cancelGraphRun", ["older-science-run"]);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Cancel run" })).toBeNull());
  });

  it("leaves blocked and up-to-date outputs alone", async () => {
    const { user, request } = setup();
    request.mockResolvedValueOnce(null).mockResolvedValueOnce({ previewToken: "x", steps: [
      { ...step, status: "current" }, { ...step, nodeId: "edited", status: "blocked", detail: "The report has manual edits." },
    ] });
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    await user.click(screen.getByRole("button", { name: "Preview run" }));
    const preview = await screen.findByRole("region", { name: "Run preview" });
    expect(preview).toHaveTextContent("Up to date");
    expect(preview).toHaveTextContent("The report has manual edits.");
    expect(screen.queryByRole("button", { name: "Run graph" })).toBeNull();
  });
});
