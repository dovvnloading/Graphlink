import { ReactFlowProvider, useNodes } from "@xyflow/react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeRequestOnlyTransport } from "../../lib/ws/transport.testUtils";
import { SceneStore } from "../canvas/sceneStore";
import { downloadBlob } from "../canvas/downloadTextFile";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import { GraphRunnerDialog } from "./GraphRunnerDialog";
import type { GraphRunTrace } from "./graphRunTrace";

vi.mock("../canvas/downloadTextFile", () => ({ downloadBlob: vi.fn() }));

function OpenRunner() {
  const overlays = useOverlays();
  return <button type="button" onClick={() => overlays.open("graph-runner", "dialog")}>Open runner</button>;
}

function SelectedNodes() {
  const nodes = useNodes();
  return <output data-testid="selected-canvas-nodes">{nodes.filter((node) => node.selected).map((node) => node.id).join(",")}</output>;
}

const attemptId = "graph-" + "1".repeat(32);
const trace: GraphRunTrace = {
  schemaVersion: 1, runId: "saved-inspect-1", status: "reconciliation-required",
  startedAt: "2026-11-18T09:00:00+00:00", finishedAt: null, durationMs: null,
  steps: [{
    index: 1, sourceNodeId: "source", destinationNodeId: "destination",
    kind: "model", operation: "model", provider: "Ollama", model: "qwen3:8b",
    inputFingerprints: {
      input: "a".repeat(64), configuration: "b".repeat(64),
      destinationBefore: "c".repeat(64), endpoint: null,
    },
    outputVersion: { sha256: "d".repeat(64), number: 2 },
    status: "reconciliation-required",
    blockedCause: "External request outcome is uncertain; reconcile before retrying.",
    startedAt: "2026-11-18T09:00:00+00:00", finishedAt: null, durationMs: null,
    attempts: [{
      requestId: attemptId, kind: "model", provider: "Ollama", model: "qwen3:8b", tool: null,
      inputFingerprint: "a".repeat(64), phase: "reconciliation-required",
      persistedPhase: "dispatched", dispatchedAt: "2026-11-18T09:00:00+00:00",
      resolvedAt: null,
    }],
  }],
};

const run = {
  runId: trace.runId, status: trace.status, startedAt: trace.startedAt,
  steps: [{
    nodeId: "destination", sourceId: "source", label: "Reply note",
    kind: "model", operation: "model", status: "reconciliation-required",
    detail: "External request outcome is uncertain.",
    externalAttempt: {
      requestId: attemptId, kind: "model", provider: "Ollama", model: "qwen3:8b",
      tool: "", inputHash: "a".repeat(64), inputRef: "graph-text:source:hash", phase: "dispatched",
    },
  }],
  trace,
};

function setup(savedRun: unknown) {
  const user = userEvent.setup();
  const { transport, request } = makeRequestOnlyTransport();
  const store = new SceneStore(transport);
  Object.assign(store.getScene(), { nodes: [
    { id: "source", kind: "note", title: "Source note" },
    { id: "destination", kind: "model", title: "Reply note" },
  ] });
  const initialNodes = ["source", "destination"].map((id) => ({
    id, selected: false, position: { x: 0, y: 0 }, data: {},
  }));
  request.mockResolvedValueOnce(savedRun);
  render(<OverlayProvider><ReactFlowProvider initialNodes={initialNodes}>
    <OpenRunner /><GraphRunnerDialog store={store} /><SelectedNodes />
  </ReactFlowProvider></OverlayProvider>);
  return { user, request };
}

async function blobText(blob: Blob): Promise<string> {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

describe("saved Graph Runner inspector", () => {
  it("expands a dependency step, selects its canvas node and exports only trace metadata", async () => {
    const { user } = setup(run);
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    const latest = await screen.findByRole("region", { name: "Latest run" });
    await user.click(within(latest).getByRole("button", { name: "Inspect run trace (1 steps)" }));
    await user.click(within(latest).getByRole("button", { name: /Step 1 · Reply note · Reconciliation required/ }));
    await waitFor(() => expect(screen.getByTestId("selected-canvas-nodes")).toHaveTextContent("destination"));
    expect(latest).toHaveTextContent("Source note");
    expect(latest).toHaveTextContent("Output revision: 2");
    expect(latest).toHaveTextContent("Attempt history (1)");
    expect(latest).toHaveTextContent(attemptId);
    expect(latest).toHaveTextContent("Blocked cause");

    await user.click(within(latest).getByRole("button", { name: "Export trace JSON" }));
    expect(downloadBlob).toHaveBeenCalledWith(expect.any(Blob), "graph-run-saved-inspect-1.trace.json");
    const [blob] = vi.mocked(downloadBlob).mock.lastCall!;
    const exported = await blobText(blob);
    expect(JSON.parse(exported).steps[0]).toMatchObject({
      destinationNodeId: "destination",
      operation: "model", model: "qwen3:8b",
      status: "reconciliation-required",
    });
    expect(exported).not.toContain("Reply note");
    expect(exported).not.toContain("External request outcome is uncertain.");
  });

  it("exports the chosen earlier run rather than the latest run", async () => {
    const earlier = { ...run, runId: "older-saved-run", trace: { ...trace, runId: "older-saved-run" } };
    const { user } = setup({ ...run, history: [earlier] });
    await user.click(screen.getByRole("button", { name: "Open runner" }));
    await user.click(screen.getByRole("button", { name: "Earlier runs (1)" }));
    await user.click(screen.getByRole("button", { name: / · Reconciliation required$/ }));
    const inspectorButtons = screen.getAllByRole("button", { name: "Inspect run trace (1 steps)" });
    await user.click(inspectorButtons[1]);
    await user.click(screen.getByRole("button", { name: "Export trace JSON" }));
    expect(downloadBlob).toHaveBeenCalledWith(expect.any(Blob), "graph-run-older-saved-run.trace.json");
  });
});
