import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { SceneStore } from "../canvas/sceneStore";
import type { WorkflowDocument } from "../canvas/workflowLibrary";
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
    startWith={{ workflowId: "w" }} onFormClosed={() => {}} onChanged={onChanged} onRunStarted={() => {}} />);

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
