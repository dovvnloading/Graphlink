import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { WorkflowSpec } from "../../lib/workflow-contract/generated/workflow";
import { WorkflowRunsPanel } from "./WorkflowRunsPanel";
import { runWaiting, type DetachedRun, type RunSummary } from "./workflowApi";
import type { ExternalizedValue } from "./workflowRunValues";
import { downloadBlob } from "../canvas/downloadTextFile";

vi.mock("../canvas/downloadTextFile", async importOriginal => ({
  ...await importOriginal<typeof import("../canvas/downloadTextFile")>(), downloadBlob: vi.fn(),
}));

const port = { name: "value", kind: "text", required: true } as const;
const spec: WorkflowSpec = {
  schema_version: 1, workflow_id: "w", revision: "r", name: "Release notes", inputs: [port],
  steps: [
    { step_id: "write_notes", adapter: { adapter_id: "graph.local_model", adapter_version: "1", side_effect_class: "executes",
      ports: { inputs: [port], outputs: [port] } }, config_json: "{}", bindings: [{ port: "value", source: { kind: "input", port: "value" } }] },
    { step_id: "sign_off", adapter: { adapter_id: "control.approval", adapter_version: "1", side_effect_class: "pure",
      ports: { inputs: [port], outputs: [port] } }, config_json: "{}", bindings: [{ port: "value", source: { kind: "step", step_id: "write_notes", port: "value" } }] },
  ],
  outputs: [{ port, source: { kind: "step", step_id: "sign_off", port: "value" } }],
};
function run(overrides: Partial<DetachedRun>): DetachedRun {
  return {
    run_id: "run-1", status: "running", error: null, cost_usd: 0, created_at: "2026-11-24T10:00:00Z", spec,
    inputs: { value: { kind: "text", text: "secret draft" } }, outputs: {}, steps: {}, awaiting_approval: null,
    unattended_effects: ["pure"],
    preview: { fingerprint: "f", estimated_cost_usd: 0, requires_approval: true, control_flow: {},
      steps: [["write_notes", "executes", { execution_lane: "local", model_identity: ["Ollama", "qwen3:8b"], cost_usd: 0 }],
        ["sign_off", "pure", { execution_lane: "local", model_identity: null, cost_usd: 0 }]] },
    ...overrides,
  };
}
function summary(record: DetachedRun): RunSummary {
  const { run_id, preset_id, status, created_at, started_at, finished_at, cost_usd, spec } = record;
  return { run_id, preset_id, status, created_at, started_at, finished_at, cost_usd,
    spec: { name: spec.name }, waiting_for_approval: runWaiting(record) };
}
function mockFetch() {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ accepted: true }) });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); window.location.hash = ""; });

it("explains an unattended pause and approves it once with the run's nonce", async () => {
  const fetchMock = mockFetch();
  const onChanged = vi.fn();
  render(<WorkflowRunsPanel workspace={3} loaded presets={[]} selected="run-1" onSelect={() => {}} onChanged={onChanged}
    runs={[]} detailLoaded detail={run({ awaiting_approval: { step_id: "write_notes", nonce: "n-42", side_effect_class: "executes", preview_fingerprint: "f" } })} />);
  const review = screen.getByRole("group", { name: "Review Write notes" });
  expect(review).toHaveTextContent("runs a model, code or an agent on this computer, using Ollama · qwen3:8b");
  expect(review).toHaveTextContent("only lets pure steps run without you");
  expect(screen.getByText("Release notes: Waiting for approval: Write notes")).toHaveAttribute("role", "status");
  await userEvent.click(screen.getByRole("button", { name: "Approve this step once" }));
  expect(fetchMock).toHaveBeenCalledWith("/api/workflows/3/runs/run-1/step-decision", expect.objectContaining({
    method: "POST", body: JSON.stringify({ step_id: "write_notes", nonce: "n-42", approved: true }),
  }));
  expect(onChanged).toHaveBeenCalled();
});

it("approves an Approval step through its own endpoint and keeps inputs collapsed", async () => {
  const fetchMock = mockFetch();
  render(<WorkflowRunsPanel workspace={3} loaded presets={[]} selected="run-1" onSelect={() => {}} onChanged={() => {}}
    runs={[]} detailLoaded detail={run({ steps: {
      write_notes: { step_id: "write_notes", status: "completed", error: null, outputs: { value: { kind: "text", text: "Draft ready" } } },
      sign_off: { step_id: "sign_off", status: "running", error: null, approval: { status: "pending", message: "Check the notes" } },
    } })} />);
  expect(screen.getByText("secret draft")).not.toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Approve step" }));
  expect(fetchMock).toHaveBeenCalledWith("/api/workflows/3/runs/run-1/step-approval", expect.objectContaining({
    body: JSON.stringify({ step_id: "sign_off", approved: true }),
  }));
});

it("lists runs as rows that say what is waiting", () => {
  render(<WorkflowRunsPanel workspace={3} loaded presets={[{ preset_id: "p1", name: "Nightly", trigger: null, cost_usd: 0, allowed_effects: ["pure"] }]} detail={null} detailLoaded={false}
    selected="" onSelect={() => {}} onChanged={() => {}}
    runs={[run({ preset_id: "p1", awaiting_approval: { step_id: "write_notes", nonce: "n", side_effect_class: "executes", preview_fingerprint: "f" } }),
      run({ run_id: "run-2", status: "completed", finished_at: "2026-11-24T10:00:08Z", started_at: "2026-11-24T10:00:00Z" })].map(summary)} />);
  expect(screen.getByText("2 runs · 1 waiting for you")).toHaveAttribute("role", "status");
  const rows = screen.getAllByRole("button", { name: /^Release notes,/ });
  expect(rows[0]).toHaveAccessibleName(/waiting for approval/);
  expect(rows[0]).toHaveTextContent("preset “Nightly”");
  expect(rows[1]).toHaveTextContent("8 s");
});

const largeValues = [
  { kind: "text", text: "x".repeat(65_537) },
  { kind: "json", value: { text: "x".repeat(65_537) } },
  { kind: "table", columns: ["id", "data"], rows: [["001", "x".repeat(65_537)]] },
] as const;

function externalized(kind: ExternalizedValue["kind"], blob: Blob): ExternalizedValue {
  return { kind, asset_ref: "a".repeat(64), mime_type: "application/json", size_bytes: blob.size };
}

it.each(largeValues)("keeps externalized $kind results, inputs and step outputs inspectable without eager downloads", async payload => {
  const blob = new Blob([JSON.stringify(payload)]);
  const stored = externalized(payload.kind, blob);
  const fetchMock = mockFetch();
  fetchMock.mockResolvedValueOnce({ ok: true, blob: async () => blob });
  window.location.hash = "token=fixture-token";
  render(<WorkflowRunsPanel workspace={3} loaded presets={[]} selected="run-1" onSelect={() => {}} onChanged={() => {}}
    runs={[]} detailLoaded detail={run({ status: "completed", inputs: { value: stored }, outputs: { value: stored }, steps: {
      write_notes: { step_id: "write_notes", status: "completed", error: null, outputs: { value: stored } },
    } })} />);
  const result = screen.getByRole("region", { name: "Result" });
  expect(within(result).getByText(/Stored separately · 64\./)).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Inputs (1)" }));
  await userEvent.click(screen.getByRole("button", { name: "Outputs (1)" }));
  expect(screen.getAllByRole("button", { name: "Download value (JSON)" })).toHaveLength(3);
  expect(fetchMock).not.toHaveBeenCalled();
  await userEvent.click(within(result).getByRole("button", { name: "Download value (JSON)" }));
  await waitFor(() => expect(downloadBlob).toHaveBeenCalledWith(blob, `workflow-${payload.kind}-aaaaaaaaaaaa.json`));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock).toHaveBeenCalledWith("/api/workflows/3/runs/run-1/assets/" + stored.asset_ref, expect.objectContaining({
    headers: { Authorization: "Bearer fixture-token" },
  }));
  await userEvent.click(screen.getByRole("button", { name: "Save result as graph" }));
  expect(fetchMock).toHaveBeenLastCalledWith("/api/workflows/3/runs/run-1/save-as-graph", expect.objectContaining({ method: "POST" }));
  expect(await screen.findByText("Saved as a new graph")).toBeVisible();
});

it("keeps the run usable after a missing asset, and allows an explicit retry", async () => {
  const blob = new Blob([JSON.stringify(largeValues[0])]);
  const fetchMock = mockFetch();
  fetchMock.mockResolvedValueOnce({ ok: false, status: 404 })
    .mockResolvedValueOnce({ ok: true, blob: async () => new Blob(["wrong size"]) })
    .mockResolvedValueOnce({ ok: true, blob: async () => blob });
  render(<WorkflowRunsPanel workspace={3} loaded presets={[]} selected="run-1" onSelect={() => {}} onChanged={() => {}}
    runs={[]} detailLoaded detail={run({ status: "completed", outputs: { value: externalized("text", blob) } })} />);
  const download = screen.getByRole("button", { name: "Download value (JSON)" });
  await userEvent.click(download);
  expect(await screen.findByText("The stored value is missing or corrupt.")).toBeVisible();
  expect(downloadBlob).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Save result as graph" })).toBeEnabled();
  await userEvent.click(download);
  expect(await screen.findByText("The stored value has an unexpected size.")).toBeVisible();
  expect(downloadBlob).not.toHaveBeenCalled();
  await userEvent.click(download);
  await waitFor(() => expect(downloadBlob).toHaveBeenCalledTimes(1));
  expect(screen.queryByText("Couldn't download stored value")).not.toBeInTheDocument();
});

it("rejects invalid reference metadata before making a request", async () => {
  const fetchMock = mockFetch();
  render(<WorkflowRunsPanel workspace={3} loaded presets={[]} selected="run-1" onSelect={() => {}} onChanged={() => {}}
    runs={[]} detailLoaded detail={run({ status: "completed", outputs: { value: {
      kind: "table", asset_ref: "../invalid", mime_type: "application/json", size_bytes: 65_537,
    } } })} />);
  expect(screen.getByText("Stored value reference is invalid.")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Download value (JSON)" }));
  expect(await screen.findByText("This stored value reference is invalid.")).toBeVisible();
  expect(fetchMock).not.toHaveBeenCalled();
});

it("still displays inline values at the text threshold and ordinary file metadata", () => {
  const fetchMock = mockFetch();
  const text = "x".repeat(65_536);
  const asset = { asset_id: "file", sha256: "a".repeat(64), media_type: "application/octet-stream", filename: "sample.bin", size_bytes: 4 };
  const file = { kind: "file" as const, asset, asset_ref: asset.sha256, mime_type: asset.media_type };
  render(<WorkflowRunsPanel workspace={3} loaded presets={[]} selected="run-1" onSelect={() => {}} onChanged={() => {}}
    runs={[]} detailLoaded detail={run({ status: "completed", outputs: { value: { kind: "text", text }, file } })} />);
  const result = screen.getByRole("region", { name: "Result" });
  expect(within(result).getByRole("region", { name: "Value" }).textContent).toBe(text);
  expect(within(result).getByText("sample.bin (application/octet-stream)")).toBeVisible();
  expect(within(result).queryByRole("button", { name: /Download/ })).not.toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
});
