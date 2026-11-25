import { ReactFlowProvider } from "@xyflow/react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { SceneStore } from "../canvas/sceneStore";
import type { WorkflowDocument, WorkflowEntry } from "../canvas/workflowLibrary";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
import { WorkflowLibraryDialog } from "./WorkflowLibraryDialog";
import { WorkflowPresetsPanel } from "./WorkflowPresetsPanel";

afterEach(() => vi.unstubAllGlobals());

it("omits reset choice overrides from preview and save while preserving explicit empty choices", async () => {
  const user = userEvent.setup();
  const doc: WorkflowDocument = {
    format: "graphlink-workflow",
    spec: { schema_version: 1, workflow_id: "w", revision: "r", name: "Example", inputs: [], steps: [], outputs: [] },
    parameters: [
      { name: "mode", step_id: "step", setting: "mode", kind: "choice", choices: ["copy", "count"] },
      { name: "suffix", step_id: "step", setting: "suffix", kind: "choice", choices: ["!", ""] },
    ],
  };
  const store = { workflowLibrary: vi.fn().mockResolvedValue(doc) } as unknown as SceneStore;
  const preview = { fingerprint: "f", estimated_cost_usd: 0, requires_approval: false, steps: [], control_flow: {} };
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => preview });
  vi.stubGlobal("fetch", fetchMock);
  const onChanged = vi.fn();
  render(<WorkflowPresetsPanel store={store} workspace={3} presets={[]} loaded
    rows={[{ workflow_id: "w", revision: "r", version: 1, name: "Example", created_at: "" }]}
    startWith={{ workflowId: "w", revision: "r", version: 1 }} onFormClosed={() => {}} onChanged={onChanged} onRunStarted={() => {}} />);

  async function choose(field: string, option: string) {
    await user.click(await screen.findByRole("button", { name: field }));
    await user.click(screen.getByRole("option", { name: option }));
  }
  const request = { workflow_id: "w", revision: "r", inputs: {}, parameters: {} };
  async function previewWith(parameters: Record<string, string>) {
    await user.click(screen.getByRole("button", { name: "Preview preset" }));
    expect(await screen.findByRole("button", { name: "Approve and save preset" })).toBeEnabled();
    expect(fetchMock).toHaveBeenLastCalledWith("/api/workflows/3/presets/preview", expect.objectContaining({
      method: "POST", body: JSON.stringify({ ...request, parameters }),
    }));
  }

  await choose("Mode", "count");
  await previewWith({ mode: "count" });
  await choose("Mode", "Keep the saved setting");
  await previewWith({});
  await choose("Suffix", "(Empty string)");
  await previewWith({ suffix: "" });
  await choose("Mode", "copy");
  await choose("Mode", "Keep the saved setting");
  await previewWith({ suffix: "" });
  await choose("Suffix", "Keep the saved setting");
  await previewWith({});
  await user.click(screen.getByRole("button", { name: "Approve and save preset" }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
  expect(fetchMock).toHaveBeenLastCalledWith("/api/workflows/3/presets", expect.objectContaining({
    method: "POST", body: JSON.stringify({ request, name: "Example", allowed_effects: ["pure"], cost_usd: 0,
      trigger: null, fingerprint: "f", approved: true }),
  }));
});

function versionedDocument(revision: string, input: string, operation: "copy" | "word_count"): WorkflowDocument {
  const port = { name: "text", kind: "text", required: true } as const;
  return {
    format: "graphlink-workflow",
    spec: {
      schema_version: 1, workflow_id: "w", revision, name: "Versioned example",
      inputs: [{ ...port, name: input }],
      steps: [{
        step_id: operation, adapter: { adapter_id: "note.text", adapter_version: "1", side_effect_class: "pure",
          ports: { inputs: [port], outputs: [port] } },
        config_json: JSON.stringify({ operation }),
        bindings: [{ port: "text", source: { kind: "input", port: input } }],
      }],
      outputs: [{ port, source: { kind: "step", step_id: operation, port: "text" } }],
    },
    parameters: [],
  };
}

function LibraryHarness({ store }: { store: SceneStore }) {
  const { open } = useOverlays();
  return <>
    <button onClick={() => open("workflow-library", "dialog")}>Open library</button>
    <WorkflowLibraryDialog store={store} />
  </>;
}

it("opens, previews and saves a preset from the selected historical revision, not the latest", async () => {
  const user = userEvent.setup();
  const v1 = versionedDocument("r1", "old_input", "copy");
  const v2 = versionedDocument("r2", "new_input", "word_count");
  const versions = [
    { revision: "r2", version: 2, created_at: "2026-05-20T12:00:00Z" },
    { revision: "r1", version: 1, created_at: "2026-05-01T12:00:00Z" },
  ];
  const latest: WorkflowEntry = { ...versions[0], workflow_id: "w", name: "Versioned example" };
  const workflowLibrary = vi.fn(async (action: string, payload?: { revision?: string }) => {
    if (action === "list") return [latest];
    if (action === "starters") return [];
    if (action === "history") return versions;
    if (action === "get" && payload?.revision === "r1") return v1;
    if (action === "get" && payload?.revision === "r2") return v2;
    throw new Error("Unexpected library request");
  });
  const store = { workflowLibrary } as unknown as SceneStore;
  const preview = { fingerprint: "old-review", estimated_cost_usd: 0, requires_approval: false,
    steps: [], control_flow: {} };
  const fetchMock = vi.fn(async (path: string) => ({
    ok: true, json: async () => path === "/api/workflows/workspaces" ? [{ id: 3, name: "Workspace" }]
      : path.endsWith("/preview") ? preview : [],
  }));
  vi.stubGlobal("fetch", fetchMock);
  render(<ReactFlowProvider><OverlayProvider><LibraryHarness store={store} /></OverlayProvider></ReactFlowProvider>);
  await user.click(screen.getByRole("button", { name: "Open library" }));
  await user.click(await screen.findByRole("button", { name: "Open Versioned example" }));
  await user.click(await screen.findByRole("button", { name: "Version history" }));
  await user.click(screen.getByRole("option", { name: /^Version 1 ·/ }));
  const date = new Date(versions[1].created_at).toLocaleDateString(undefined, { dateStyle: "medium" });
  expect(await screen.findByText("1 step · 1 input · 1 output · version 1 · saved " + date)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "More" }));
  await user.click(screen.getByRole("menuitem", { name: "Save as a preset…" }));
  expect(await screen.findByRole("button", { name: "Workflow" })).toHaveTextContent("Versioned example · version 1");
  expect(workflowLibrary).toHaveBeenLastCalledWith("get", { workflow_id: "w", revision: "r1" });
  await user.type(await screen.findByRole("textbox", { name: "Old input" }), "original");
  expect(screen.queryByRole("textbox", { name: "New input" })).toBeNull();
  const request = { workflow_id: "w", revision: "r1", inputs: { old_input: "original" }, parameters: {} };
  await user.click(screen.getByRole("button", { name: "Preview preset" }));
  expect(await screen.findByRole("button", { name: "Approve and save preset" })).toBeEnabled();
  expect(fetchMock).toHaveBeenCalledWith("/api/workflows/3/presets/preview", expect.objectContaining({
    method: "POST", body: JSON.stringify(request),
  }));

  // Changing to the latest version is explicit and invalidates the old review.
  await user.click(screen.getByRole("button", { name: "Workflow" }));
  await user.click(screen.getByRole("option", { name: "Versioned example · version 2" }));
  expect(await screen.findByRole("textbox", { name: "New input" })).toHaveValue("");
  expect(screen.queryByRole("button", { name: "Approve and save preset" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Workflow" }));
  await user.click(screen.getByRole("option", { name: "Versioned example · version 1" }));
  await user.type(await screen.findByRole("textbox", { name: "Old input" }), "original");
  await user.click(screen.getByRole("button", { name: "Preview preset" }));
  await user.click(await screen.findByRole("button", { name: "Approve and save preset" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/workflows/3/presets", expect.objectContaining({
    method: "POST", body: JSON.stringify({ request, name: "Versioned example", allowed_effects: ["pure"], cost_usd: 0,
      trigger: null, fingerprint: "old-review", approved: true }),
  })));

  // A fresh preset opened directly from the list still defaults to latest.
  await user.click(await screen.findByRole("button", { name: "New preset" }));
  expect(await screen.findByRole("button", { name: "Workflow" })).toHaveTextContent("Versioned example · version 2");
  expect(await screen.findByRole("textbox", { name: "New input" })).toBeVisible();
});

it("keeps a reviewed preset pinned across a library refresh", async () => {
  const user = userEvent.setup();
  const v1 = versionedDocument("r1", "old_input", "copy");
  const workflowLibrary = vi.fn().mockResolvedValue(v1);
  const store = { workflowLibrary } as unknown as SceneStore;
  const preview = { fingerprint: "f", estimated_cost_usd: 0, requires_approval: false, steps: [], control_flow: {} };
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => preview });
  vi.stubGlobal("fetch", fetchMock);
  const row: WorkflowEntry = { workflow_id: "w", name: "Versioned example", revision: "r1", version: 1, created_at: "" };
  const props = { store, workspace: 3, presets: [], loaded: true, startWith: null,
    onFormClosed: () => {}, onChanged: () => {}, onRunStarted: () => {} };
  const { rerender } = render(<WorkflowPresetsPanel {...props} rows={[row]} />);
  await user.click(screen.getByRole("button", { name: "New preset" }));
  await user.type(await screen.findByRole("textbox", { name: "Old input" }), "keep me");
  await user.click(screen.getByRole("button", { name: "Preview preset" }));
  expect(await screen.findByRole("button", { name: "Approve and save preset" })).toBeEnabled();
  rerender(<WorkflowPresetsPanel {...props} rows={[{ ...row, revision: "r2", version: 2 }]} />);
  expect(screen.getByRole("button", { name: "Workflow" })).toHaveTextContent("Versioned example · version 1");
  expect(screen.getByRole("textbox", { name: "Old input" })).toHaveValue("keep me");
  expect(workflowLibrary).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "Approve and save preset" }));
  await waitFor(() => expect(fetchMock).toHaveBeenLastCalledWith("/api/workflows/3/presets", expect.objectContaining({
    method: "POST", body: JSON.stringify({
      request: { workflow_id: "w", revision: "r1", inputs: { old_input: "keep me" }, parameters: {} },
      name: "Versioned example", allowed_effects: ["pure"], cost_usd: 0, trigger: null, fingerprint: "f", approved: true,
    }),
  })));
});

it("does not fall back to latest when the requested revision cannot be loaded", async () => {
  const user = userEvent.setup();
  const workflowLibrary = vi.fn().mockRejectedValue(new Error("This pinned workflow version is not in the library."));
  const store = { workflowLibrary } as unknown as SceneStore;
  render(<WorkflowPresetsPanel store={store} workspace={3} presets={[]} loaded
    rows={[{ workflow_id: "w", revision: "r2", version: 2, name: "Example", created_at: "" }]}
    startWith={{ workflowId: "w", revision: "r1", version: 1 }} onFormClosed={() => {}} onChanged={() => {}} onRunStarted={() => {}} />);
  expect(await screen.findByText("This pinned workflow version is not in the library.")).toBeVisible();
  expect(workflowLibrary).toHaveBeenCalledExactlyOnceWith("get", { workflow_id: "w", revision: "r1" });
  expect(screen.getByRole("button", { name: "Preview preset" })).toBeDisabled();
  // The latest revision is available only after an explicit choice.
  workflowLibrary.mockResolvedValue(versionedDocument("r2", "new_input", "word_count"));
  await user.click(screen.getByRole("button", { name: "Workflow" }));
  await user.click(screen.getByRole("option", { name: "Example · version 2" }));
  expect(await screen.findByRole("textbox", { name: "New input" })).toBeVisible();
});
