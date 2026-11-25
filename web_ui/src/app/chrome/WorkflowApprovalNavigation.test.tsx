import { ReactFlowProvider } from "@xyflow/react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { WorkflowSpec } from "../../lib/workflow-contract/generated/workflow";
import type { SceneStore } from "../canvas/sceneStore";
import type { WorkflowDocument } from "../canvas/workflowLibrary";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import { toast, Toaster } from "../ui/Toast";
import { WorkflowApprovalNotifications } from "./WorkflowApprovalNotifications";
import { WorkflowLibraryDialog } from "./WorkflowLibraryDialog";
import { runWaiting, type DetachedRun, type RunSummary } from "./workflowApi";

const port = { name: "value", kind: "text", required: true } as const;
const spec: WorkflowSpec = {
  schema_version: 1, workflow_id: "w", revision: "r", name: "Waiting workflow", inputs: [port],
  steps: [{ step_id: "review", adapter: {
    adapter_id: "control.approval", adapter_version: "1", side_effect_class: "pure",
    ports: { inputs: [port], outputs: [port] },
  }, config_json: JSON.stringify({ kind: "text", message: "Check it" }),
  bindings: [{ port: "value", source: { kind: "input", port: "value" } }] }],
  outputs: [{ port, source: { kind: "step", step_id: "review", port: "value" } }],
};
const waiting: DetachedRun = {
  run_id: "target", status: "running", error: null, cost_usd: 0, created_at: "2026-11-24T10:00:00Z", spec,
  inputs: { value: { kind: "text", text: "input" } }, outputs: {},
  steps: { review: { step_id: "review", status: "running", error: null, approval: { status: "pending", message: "Check it" } } },
  preview: { fingerprint: "f", estimated_cost_usd: 0, requires_approval: false, steps: [], control_flow: {} },
};
const other: DetachedRun = { ...waiting, run_id: "other", status: "completed", steps: {},
  spec: { ...spec, name: "Other workflow" } };
function summary(record: DetachedRun): RunSummary {
  const { run_id, preset_id, status, created_at, started_at, finished_at, cost_usd, spec } = record;
  return { run_id, preset_id, status, created_at, started_at, finished_at, cost_usd,
    spec: { name: spec.name }, waiting_for_approval: runWaiting(record) };
}
const doc: WorkflowDocument = { format: "graphlink-workflow", spec, parameters: [] };

function Harness({ store }: { store: SceneStore }) {
  const { open, isOpen } = useOverlays();
  return <>
    <button onClick={() => open("workflow-library", "dialog")}>Open library</button>
    <WorkflowApprovalNotifications connected />
    <WorkflowLibraryDialog store={store} />
    <Toaster excludeScope={isOpen("workflow-library") ? "workflow-library" : undefined} />
  </>;
}

function setup(options: { workspace?: number; firstRuns?: DetachedRun[] | Promise<DetachedRun[]>; targetRuns?: DetachedRun[] | Promise<DetachedRun[]> } = {}) {
  const workspace = options.workspace ?? 3;
  const workflowLibrary = vi.fn(async (action: string) => {
    if (action === "list") return [{ workflow_id: "w", revision: "r", version: 1, name: spec.name, created_at: "" }];
    if (action === "starters") return [];
    if (action === "history") return [{ revision: "r", version: 1, created_at: "" }];
    if (action === "get") return doc;
    throw new Error("Unexpected library request: " + action);
  });
  const store = { workflowLibrary } as unknown as SceneStore;
  const fetchMock = vi.fn(async (path: string, _init?: RequestInit) => {
    const response = (body: unknown, status = 200) => ({ ok: status === 200, status, json: async () => body });
    if (path === "/api/workflows/active-approvals") return response([{
      workspace_id: workspace, run_id: "target", workflow: spec.name, step_id: "review", message: "Check it",
    }]);
    if (path === "/api/workflows/workspaces") return response([{ id: 3, name: "First" }, { id: 4, name: "Second" }]);
    const match = /^\/api\/workflows\/(3|4)\/runs(?:\?summary=true|\/([^/]+))$/.exec(path);
    if (match) {
      const records = await (match[1] === "3" ? options.firstRuns ?? [other, ...(workspace === 3 ? [waiting] : [])] : options.targetRuns ?? [waiting]);
      if (!match[2]) return response(records.map(summary));
      const record = records.find(run => run.run_id === match[2]);
      return record ? response(record) : response({ detail: "Workflow run not found." }, 404);
    }
    if (path.endsWith("/presets?summary=true")) return response([]);
    if (path.endsWith("/step-approval")) return response({ accepted: true });
    throw new Error("Unexpected workflow request: " + path);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<ReactFlowProvider><OverlayProvider><Harness store={store} /></OverlayProvider></ReactFlowProvider>);
  return { fetchMock, user: userEvent.setup() };
}

afterEach(() => { toast.clear(); vi.unstubAllGlobals(); });

it.each(["JSON launcher", "preset form", "another run"] as const)("opens the waiting run from %s without approval or submission", async source => {
  const { user, fetchMock } = setup();
  await user.click(screen.getByRole("button", { name: "Open library" }));
  const dialog = within(screen.getByRole("dialog", { name: "Workflow library" }));
  const review = await screen.findByRole("button", { name: "Review run" });
  if (source === "preset form") {
    // Start from the library detail, exercising the pending presetStart request too.
    await user.click(await dialog.findByRole("button", { name: "Open Waiting workflow" }));
    await user.click(dialog.getByRole("button", { name: "More" }));
    await user.click(screen.getByRole("menuitem", { name: "Save as a preset…" }));
    const name = await dialog.findByRole("textbox", { name: "Preset name" });
    await user.clear(name);
    await user.type(name, "unsaved preset");
  } else {
    await user.click(dialog.getByRole("tab", { name: /^Runs/ }));
    await dialog.findByRole("button", { name: /^Other workflow,/ });
    if (source === "JSON launcher") {
      await user.click(dialog.getByRole("button", { name: "Run from JSON" }));
      await user.type(dialog.getByRole("textbox", { name: "Workflow request JSON (spec and parameters)" }), "unsaved draft");
    } else {
      await user.click(dialog.getByRole("button", { name: /^Other workflow,/ }));
      expect(await dialog.findByRole("heading", { name: "Other workflow" })).toBeVisible();
    }
  }
  await user.click(review);
  expect(await dialog.findByRole("heading", { name: "Waiting workflow" })).toBeVisible();
  expect(dialog.getByRole("heading", { name: "Selected run" })).toHaveFocus();
  expect(dialog.getByRole("button", { name: "Approve step" })).toBeEnabled();
  expect(dialog.queryByRole("textbox", { name: "Workflow request JSON (spec and parameters)" })).toBeNull();
  expect(dialog.queryByRole("textbox", { name: "Preset name" })).toBeNull();
  expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  if (source === "preset form") {
    await user.click(dialog.getByRole("tab", { name: /^Presets/ }));
    expect(dialog.getByRole("button", { name: "New preset" })).toBeVisible();
    expect(dialog.queryByRole("textbox", { name: "Preset name" })).toBeNull();
  } else if (source === "JSON launcher") {
    await user.click(dialog.getByRole("button", { name: "All runs" }));
    await user.click(dialog.getByRole("button", { name: "Run from JSON" }));
    expect(dialog.getByRole("textbox", { name: "Workflow request JSON (spec and parameters)" })).toHaveValue("");
  }
});

it("clears another workspace's receipts while loading the notification's exact target", async () => {
  let finishTarget!: (runs: DetachedRun[]) => void;
  const targetRuns = new Promise<DetachedRun[]>(resolve => { finishTarget = resolve; });
  // Deliberately reuse the ID in the first workspace to expose stale data.
  const stale = { ...other, run_id: "target" };
  const { user, fetchMock } = setup({ workspace: 4, firstRuns: [stale], targetRuns });
  await user.click(screen.getByRole("button", { name: "Open library" }));
  const dialog = within(screen.getByRole("dialog", { name: "Workflow library" }));
  await user.click(dialog.getByRole("tab", { name: /^Runs/ }));
  await user.click(await dialog.findByRole("button", { name: /^Other workflow,/ }));
  await user.click(await screen.findByRole("button", { name: "Review run" }));
  expect(dialog.getByText("Loading the run…")).toBeVisible();
  expect(dialog.queryByRole("heading", { name: "Other workflow" })).toBeNull();
  expect(dialog.queryByRole("button", { name: "Approve step" })).toBeNull();
  await act(async () => { finishTarget([waiting]); });
  expect(await dialog.findByRole("heading", { name: "Waiting workflow" })).toBeVisible();
  expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
  await user.click(dialog.getByRole("button", { name: "Approve step" }));
  expect(fetchMock).toHaveBeenCalledWith("/api/workflows/4/runs/target/step-approval", expect.objectContaining({
    method: "POST", body: JSON.stringify({ step_id: "review", approved: true }),
  }));
});

it("ignores a late old-workspace response after following a notification", async () => {
  let finishOld!: (runs: DetachedRun[]) => void;
  const firstRuns = new Promise<DetachedRun[]>(resolve => { finishOld = resolve; });
  const { user, fetchMock } = setup({ workspace: 4, firstRuns });
  await user.click(screen.getByRole("button", { name: "Open library" }));
  const dialog = within(screen.getByRole("dialog", { name: "Workflow library" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/workflows/3/runs?summary=true", expect.anything()));
  await user.click(await screen.findByRole("button", { name: "Review run" }));
  expect(await dialog.findByRole("heading", { name: "Waiting workflow" })).toBeVisible();
  await act(async () => { finishOld([{ ...other, run_id: "target" }]); });
  expect(dialog.getByRole("heading", { name: "Waiting workflow" })).toBeVisible();
  expect(dialog.queryByRole("heading", { name: "Other workflow" })).toBeNull();
});

it("shows a recoverable unavailable state when the notification target has expired", async () => {
  const { user, fetchMock } = setup({ workspace: 4, targetRuns: [] });
  await user.click(screen.getByRole("button", { name: "Open library" }));
  const dialog = within(screen.getByRole("dialog", { name: "Workflow library" }));
  await user.click(await screen.findByRole("button", { name: "Review run" }));
  expect(await dialog.findByText(/This run is no longer available in this workspace/)).toBeVisible();
  expect(dialog.queryByRole("button", { name: "Approve step" })).toBeNull();
  await user.click(dialog.getByRole("button", { name: "All runs" }));
  expect(await dialog.findByText("No runs yet")).toBeVisible();
  expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
});

it("opens directly into the notification's workspace when the library was closed", async () => {
  const { user, fetchMock } = setup({ workspace: 4 });
  await user.click(await screen.findByRole("button", { name: "Review run" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Workflow library" }));
  expect(await dialog.findByRole("heading", { name: "Waiting workflow" })).toBeVisible();
  expect(dialog.getByRole("button", { name: "Approve step" })).toBeEnabled();
  expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
});

it("moves the same approval notice into the modal without duplicating or approving it", async () => {
  const { user, fetchMock } = setup();
  expect(await screen.findByRole("button", { name: "Review run" })).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Open library" }));
  const dialog = within(screen.getByRole("dialog", { name: "Workflow library" }));
  const action = await dialog.findByRole("button", { name: "Review run" });
  expect(screen.getAllByRole("button", { name: "Review run" })).toHaveLength(1);
  action.focus();
  await user.keyboard("{Enter}");
  expect(await dialog.findByRole("heading", { name: "Waiting workflow" })).toBeVisible();
  expect(dialog.getByRole("button", { name: "Approve step" })).toBeEnabled();
  expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true);
});

it("uses only summaries until a receipt is opened and stops detail polling on another tab", async () => {
  const { user, fetchMock } = setup();
  await user.click(screen.getByRole("button", { name: "Open library" }));
  const dialog = within(screen.getByRole("dialog", { name: "Workflow library" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/workflows/3/runs?summary=true", expect.anything()));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/workflows/3/presets?summary=true", expect.anything()));
  const count = (path: string) => fetchMock.mock.calls.filter(([url]) => url === path).length;
  expect(count("/api/workflows/3/runs")).toBe(0);
  expect(count("/api/workflows/3/presets")).toBe(0);
  expect(count("/api/workflows/3/runs/target")).toBe(0);
  await user.click(dialog.getByRole("tab", { name: /^Runs/ }));
  await user.click(await dialog.findByRole("button", { name: /^Other workflow,/ }));
  expect(await dialog.findByRole("heading", { name: "Other workflow" })).toBeVisible();
  expect(count("/api/workflows/3/runs/other")).toBe(1);
  await user.click(await screen.findByRole("button", { name: "Review run" }));
  expect(await dialog.findByRole("heading", { name: "Waiting workflow" })).toBeVisible();
  const detailCall = fetchMock.mock.calls.find(([url]) => url === "/api/workflows/3/runs/target");
  expect(detailCall?.[1]?.signal?.aborted).toBe(false);
  await user.click(dialog.getByRole("tab", { name: /^Workflows/ }));
  expect(detailCall?.[1]?.signal?.aborted).toBe(true);
});

it("uses fast polling only for active runs and slows inactive or idle history", async () => {
  vi.useFakeTimers();
  try {
    const { fetchMock } = setup({ firstRuns: [other, waiting] });
    const count = (path: string) => fetchMock.mock.calls.filter(([url]) => url === path).length;
    const runsPath = "/api/workflows/3/runs?summary=true";
    const presetsPath = "/api/workflows/3/presets?summary=true";
    const click = async (element: HTMLElement) => { await act(async () => { fireEvent.click(element); }); };
    const advance = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
    await click(screen.getByRole("button", { name: "Open library" }));
    const dialog = within(screen.getByRole("dialog", { name: "Workflow library" }));
    expect(count(runsPath)).toBeGreaterThan(0);
    expect(count(presetsPath)).toBeGreaterThan(0);
    const initialRuns = count(runsPath), initialPresets = count(presetsPath);
    await advance(6000);
    expect(count(runsPath)).toBe(initialRuns);
    expect(count(presetsPath)).toBe(initialPresets);

    await click(dialog.getByRole("tab", { name: /^Runs/ }));
    const activeRuns = count(runsPath), inactivePresets = count(presetsPath);
    expect(activeRuns).toBeGreaterThan(initialRuns);
    await advance(2000);
    expect(count(runsPath)).toBe(activeRuns + 1);
    expect(count(presetsPath)).toBe(inactivePresets);

    await click(dialog.getByRole("button", { name: /^Other workflow,/ }));
    const completedPath = "/api/workflows/3/runs/other";
    expect(dialog.getByRole("heading", { name: "Other workflow" })).toBeVisible();
    const completedReads = count(completedPath);
    expect(completedReads).toBe(1);
    await advance(6000);
    expect(count(completedPath)).toBe(completedReads);

    await click(dialog.getByRole("tab", { name: /^Presets/ }));
    const visiblePresets = count(presetsPath);
    await advance(14000);
    expect(count(presetsPath)).toBe(visiblePresets);
    await advance(1000);
    expect(count(presetsPath)).toBe(visiblePresets + 1);
  } finally {
    vi.useRealTimers();
  }
});

it("polls idle history less often even on the Runs tab", async () => {
  vi.useFakeTimers();
  try {
    const { fetchMock } = setup({ firstRuns: [other] });
    const path = "/api/workflows/3/runs?summary=true";
    const count = () => fetchMock.mock.calls.filter(([url]) => url === path).length;
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Open library" })); });
    const dialog = within(screen.getByRole("dialog", { name: "Workflow library" }));
    await act(async () => { fireEvent.click(dialog.getByRole("tab", { name: /^Runs/ })); });
    const initial = count();
    expect(initial).toBeGreaterThan(0);
    await act(async () => { await vi.advanceTimersByTimeAsync(14000); });
    expect(count()).toBe(initial);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(count()).toBe(initial + 1);
  } finally {
    vi.useRealTimers();
  }
});
