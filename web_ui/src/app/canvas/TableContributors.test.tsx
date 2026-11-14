import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";
import type { SceneStore } from "./sceneStore";
import { TableContributors } from "./TableContributors";

const chart = {
  id: "chart", kind: "chart", chartData: { labels: ["A", "B"] }, tableInputSourceId: "source",
  tableSourceHash: "current", tableLabelColumn: "Team", tableValueColumn: "Cost", tableInputStatus: "current",
} as SceneNodeRow;

function lookup(getTableContributors: SceneStore["getTableContributors"]) {
  return { getTableContributors, getScene: () => ({ nodes: [] }) } as unknown as SceneStore;
}

function page(group: string, offset: number, total: number, hash = "current") {
  const rows = Array.from({ length: Math.min(50, total - offset) }, (_, i) => ({
    rowNumber: (i + offset) * 2 + 1, value: "2.00",
  }));
  return { sourceHash: hash, group, offset, totalRows: total, rows };
}

describe("source rows for a computed group", () => {
  it("fetches on demand and pages through original row numbers and raw numeric cells", async () => {
    const request = vi.fn(async (_id: string, group: string, offset: number) => page(group, offset, 51));
    const store = lookup(request);
    render(<TableContributors node={chart} store={store} />);
    expect(request).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Inspect source rows" }));
    expect(await screen.findByText("1–50 of 51")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Next source rows" }));
    const firstRow = (await screen.findAllByRole("row"))[1];
    expect(within(firstRow).getByText("101")).toBeInTheDocument();
    expect(within(firstRow).getByText("2.00")).toBeInTheDocument();
    expect(request).toHaveBeenLastCalledWith("chart", "A", 50);
    expect(screen.getByRole("button", { name: "Next source rows" })).toBeDisabled();
  });

  it("discards a late reply from another selection and rejects a mismatched source hash", async () => {
    let finishFirst!: (result: ReturnType<typeof page>) => void;
    const delayed = new Promise<ReturnType<typeof page>>((resolve) => { finishFirst = resolve; });
    const request = vi.fn(async (_id: string, group: string) => group === "A" ? delayed : page(group, 0, 1, "old"));
    render(<TableContributors node={chart} store={lookup(request)} />);
    fireEvent.click(screen.getByRole("button", { name: "Inspect source rows" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Inspect group" }), { target: { value: "B" } });
    expect(await screen.findByRole("alert")).toHaveTextContent("result changed");
    await act(async () => { finishFirst(page("A", 0, 1)); });
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveValue("B");
  });
});
