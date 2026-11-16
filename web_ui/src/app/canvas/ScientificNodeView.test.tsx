import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import type { WsTransport } from "../../lib/ws/transport";
import { SceneStore } from "./sceneStore";
import { ScientificNodeView, type ScientificFlowNode } from "./ScientificNodeView";
import { readScienceState, type ScienceState, type ScienceResult } from "./scientificWorkbench";
import * as download from "./downloadTextFile";

const operation = {
  id: "integral", title: "Integrate samples", description: "Sum trapezoids between consecutive samples.",
  fields: [{ key: "samples", label: "Samples", kind: "vector" as const, unit: "m", description: "Ordered observations." }],
  example: { samples: [1, 2, 3] },
};
const operation2 = {
  id: "scale", title: "Scale length", description: "Scale a measured length.",
  fields: [{ key: "length", label: "Length", kind: "number" as const, unit: "m", description: "" }],
  example: { length: 3 },
};
const state: ScienceState = { scienceVersion: 1, packId: "mathematics", operations: [operation, operation2], latest: null };
const result: ScienceResult = {
  operationId: "integral", inputs: { samples: [10, 20, 30] }, summary: "Computed 3 samples.", values: { distance: 0.125 },
  table: { columns: ["x", "y"], rows: [[0, 1], [2, 8], [10, 3]] },
  plot: { x: [0, 2, 10], y: [1, 8, 3], title: "Distance", xLabel: "Time (s)", yLabel: "Length (m)" },
  provenance: { packId: "mathematics", packVersion: "1.0.0", resultHash: "abc", createdAt: "2026-11-16T16:00:00Z" },
};
const parent = { title: "Samples", nodeId: "parent", raw: JSON.stringify({ ...state, latest: result }) };

function mount(raw = JSON.stringify(state), parents = [parent]) {
  const request = vi.fn<WsTransport["request"]>().mockResolvedValue({ ok: true });
  const store = new SceneStore({ request } as unknown as WsTransport);
  function draw(value: string) {
    const props = { id: "target", data: { title: "Mathematics", raw: value, pluginId: "science_mathematics", parents, store } } as NodeProps<ScientificFlowNode>;
    return <ScientificNodeView {...props} />;
  }
  const view = render(draw(raw), { wrapper: ReactFlowProvider });
  return { request, refresh: (value: string) => view.rerender(draw(value)) };
}

describe("scientific workbench card", () => {
  it("only runs on submit, validates numeric arrays, and preserves the draft on a backend error", async () => {
    const { request } = mount();
    const input = screen.getByLabelText("Samples (m)");
    fireEvent.change(input, { target: { value: "[123, null]" } });
    fireEvent.click(screen.getByText("Run calculation"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Samples must be an array of numbers");
    expect(request).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "[3,2,1]" } });
    request.mockResolvedValueOnce({ ok: false, error: "Times must increase." });
    fireEvent.click(screen.getByText("Run calculation"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Times must increase.");
    expect(input).toHaveValue("[3,2,1]");
    fireEvent.click(screen.getByText("Load example"));
    expect(input).toHaveValue("[1,2,3]");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("imports a connected column with its provenance, clears the citation when edited, and switches operations", async () => {
    const { request } = mount();
    const source = screen.getByRole("combobox", { name: "Import Samples from a connected result" });
    const index = Array.from((source as HTMLSelectElement).options).find((o) => o.text === "Samples · y")!.value;
    fireEvent.change(source, { target: { value: index } });
    expect(screen.getByLabelText("Samples (m)")).toHaveValue("[1,8,3]");
    expect(request).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Run calculation"));
    expect(request).toHaveBeenCalledWith("app-plugins", "invokePluginIntent", ["science_mathematics", "run", {
      node_id: "target", operation_id: "integral", inputs: { samples: [1, 8, 3] },
      sources: { samples: { nodeId: "parent", resultHash: "abc", selector: ["column", 1] } },
    }]);
    await waitFor(() => expect(screen.getByText("Run calculation")).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Samples (m)"), { target: { value: "[2]" } });
    fireEvent.click(screen.getByText("Run calculation"));
    expect(request.mock.lastCall?.[2]?.[2]).toMatchObject({ inputs: { samples: [2] }, sources: {} });
    await waitFor(() => expect(screen.getByText("Run calculation")).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Operation"), { target: { value: "scale" } });
    expect(screen.getByLabelText("Length (m)")).toHaveValue("3");
    fireEvent.change(screen.getByLabelText("Length (m)"), { target: { value: ".5e1" } });
    fireEvent.click(screen.getByText("Run calculation"));
    expect(request.mock.lastCall?.[2]?.[2]).toMatchObject({ operation_id: "scale", inputs: { length: 5 } });
    await waitFor(() => expect(screen.getByText("Run calculation")).toBeEnabled());
  });

  it("renders persisted runs, plots irregular x coordinates, and exports the committed artifacts", async () => {
    const save = vi.spyOn(download, "downloadBlob").mockImplementation(() => {});
    try {
      const { request } = mount(JSON.stringify({ ...state, latest: result }));
      expect(screen.getByLabelText("Samples (m)")).toHaveValue("[10,20,30]");
      expect(screen.getByRole("status")).toHaveTextContent("Computed 3 samples.");
      expect(screen.getByRole("img").querySelectorAll("circle")[1].getAttribute("cx")).toBe("70");
      expect(screen.getByRole("table")).toBeInTheDocument();
      fireEvent.click(screen.getByText("Inputs and provenance"));
      expect(screen.getByText(/"resultHash": "abc"/)).toBeVisible();
      fireEvent.click(screen.getByText("Export JSON"));
      fireEvent.click(screen.getByText("Export CSV"));
      expect(save.mock.calls.map(([, name]) => name)).toEqual(["mathematics-integral.json", "mathematics-integral.csv"]);
      expect(JSON.parse(await save.mock.calls[0][0].text())).toEqual(result);
      expect(await save.mock.calls[1][0].text()).toBe("x,y\r\n0,1\r\n2,8\r\n10,3");
      expect(request).not.toHaveBeenCalled();
    } finally { save.mockRestore(); }
  });

  it("retains the connected source label when a reopened parent has a new session id", () => {
    const reference = { nodeId: "prior-session-parent", sourceKey: "persistent-key", resultHash: "abc", selector: ["column", 1] };
    mount(JSON.stringify({ ...state, latest: { ...result, provenance: {
      ...result.provenance, sources: { samples: reference },
    } } }), [{ ...parent, raw: JSON.stringify({ ...state, nodeKey: "persistent-key", latest: result }) }]);
    expect(screen.getByText("Source · Samples · y")).toBeVisible();
  });

  it("shows the server's recovery instructions when a saved result asset is missing", () => {
    mount(JSON.stringify({ ...state, operations: [], error: "Restore the missing result asset." }));
    expect(screen.getByRole("alert")).toHaveTextContent("Restore the missing result asset.");
    expect(screen.queryByText("Run calculation")).toBeNull();
  });

  it("rejects damaged or unsupported state without rendering a run button", () => {
    mount(JSON.stringify({ ...state, scienceVersion: 2 }));
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
    expect(screen.queryByText("Run calculation")).toBeNull();
    expect(readScienceState(JSON.stringify({ ...state, latest: { ...result, plot: { ...result.plot, y: [] } } }))).toBeNull();
  });
});
