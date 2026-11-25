import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { WorkflowSpec } from "../../lib/workflow-contract/generated/workflow";
import { WorkflowRunsPanel } from "./WorkflowRunsPanel";
import { runWaiting, type DetachedRun, type RunSummary } from "./workflowApi";

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
afterEach(() => { vi.unstubAllGlobals(); });

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
