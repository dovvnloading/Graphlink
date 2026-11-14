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

function setup(selectedIds: string[] = []) {
  const user = userEvent.setup();
  const { transport, request } = makeRequestOnlyTransport();
  const store = new SceneStore(transport);
  const nodes = selectedIds.map((id) => ({ id, selected: true, position: { x: 0, y: 0 }, data: {} }));
  render(<OverlayProvider><ReactFlowProvider initialNodes={nodes}>
    <OpenRunner /><GraphRunnerDialog store={store} />
  </ReactFlowProvider></OverlayProvider>);
  return { user, request };
}

describe("Graph Runner", () => {
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
