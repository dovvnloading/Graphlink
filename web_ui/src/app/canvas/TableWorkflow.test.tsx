import { ReactFlowProvider } from "@xyflow/react";
import { cleanup, fireEvent, render as renderElement, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";
import type { WsTransport } from "../../lib/ws/transport";
import { SceneStore } from "./sceneStore";
import TableSource from "./TableSource";
import TableOutput from "./TableOutput";
import { readTableCsv, writeTableCsv } from "./tableCsv";

afterEach(cleanup);

function render(ui: ReactElement) {
  return renderElement(ui, { wrapper: ReactFlowProvider });
}

function makeStore() {
  const request = vi.fn<WsTransport["request"]>().mockResolvedValue(undefined);
  const store = new SceneStore({ request } as unknown as WsTransport);
  return { request, store };
}

const csv = "Team,Hours\r\nDesign,2\r\nEngineering,4";
const output = {
  id: "result", tableOutputVersion: 2, tableSourceHash: "b".repeat(64), tableHasPrevious: true, tableInputSourceId: "table",
  tableLabelColumn: "Team", tableValueColumn: "Hours", tableInputStatus: "stale",
} as SceneNodeRow;

describe("table source editing", () => {
  it("preserves embedded CRLFs, quotes, delimiters, empty fields and non-ASCII text", () => {
    const rows = [["A", "B", "C"], ["x, y", 'He said "yes"', "résumé\r\n続き"], ["", "", ""]];
    expect(readTableCsv(writeTableCsv(rows))).toEqual(rows);
    expect(readTableCsv("A,B\rX,Y\r")).toEqual([["A", "B"], ["X", "Y"]]);
  });

  it("commits a cell edit on Save with its exact base", async () => {
    const { request, store } = makeStore();
    render(<TableSource id="a" content={csv} store={store} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit table" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Hours, row 1" }), { target: { value: "6" } });
    expect(request).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Save table" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Edit table" })).toBeEnabled());
    expect(request).toHaveBeenCalledWith("scene", "setTableCsv", ["a", csv.replace("Design,2", "Design,6"), csv]);
  });

  it("preserves rejected CSV and concurrent changes without overwriting either version", async () => {
    const { request, store } = makeStore();
    request.mockRejectedValueOnce(new Error("Data row 1 has 1 cell; expected 2."));
    const view = render(<TableSource id="a" content={csv} store={store} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit CSV" }));
    fireEvent.change(screen.getByRole("textbox", { name: "CSV data" }), { target: { value: "a,b\nx" } });
    fireEvent.click(screen.getByRole("button", { name: "Save table" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Data row 1");
    expect(screen.getByRole("textbox", { name: "CSV data" })).toHaveValue("a,b\nx");
    view.rerender(<TableSource id="a" content={csv + "\r\nOps,3"} store={store} />);
    expect(screen.getByRole("button", { name: "Save table" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(within(screen.getByRole("table")).getByText("Ops")).toBeInTheDocument();
  });

  it("uses the configured columns for each independent consumer", async () => {
    const { request, store } = makeStore();
    render(<TableSource id="a" content={'Team,Hours,Cost\nDev,2,10'} store={store} />);
    fireEvent.change(screen.getByRole("combobox", { name: "Sum column" }), {target: {value: "Cost"}});
    fireEvent.click(screen.getByRole("button", { name: "Create chart" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Create report" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Create report" }));
    expect(request.mock.calls).toEqual([
      ["scene", "addTableOutput", ["a", "chart", "Team", "Cost"]],
      ["scene", "addTableOutput", ["a", "artifact", "Team", "Cost"]],
    ]);
    await waitFor(() => expect(screen.getByRole("button", { name: "Create report" })).toBeEnabled());
  });

  it("loads UTF-8 CSV into the draft without committing", async () => {
    const { request, store } = makeStore();
    render(<TableSource id="a" content={csv} store={store} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit CSV" }));
    const imported = 'Language,Count\n日本語,3';
    const input = screen.getByLabelText("Import CSV file");
    fireEvent.change(input, {target: {files: [new File([imported], "counts.csv", {type: "text/csv"})]}});
    await waitFor(() => expect(screen.getByRole("textbox", {name: "CSV data"})).toHaveValue(imported));
    expect(input).toHaveValue("");
    expect(request).not.toHaveBeenCalled();
  });
});

describe("table output controls", () => {
  const version = (n: number, labels: string[], values: number[]) => ({
    version: n, sourceHash: (n === 2 ? "b" : "a").repeat(64), chartData: {labels, values},
  });
  const history = {
    current: version(2, ["Design", "Ops", "Same"], [6, 1, 4]),
    previous: version(1, ["Design", "Sales", "Same"], [5, 2, 4]),
    revisionToken: "e".repeat(64),
  };

  it("compares categories and restores only the reviewed pair", async () => {
    const { request, store } = makeStore();
    request.mockResolvedValueOnce(history).mockRejectedValueOnce(new Error("Saved versions changed. Reopen the comparison."));
    render(<TableOutput node={output} store={store} />);
    expect(request).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Compare versions" }));
    const table = await screen.findByRole("table", { name: "Changes in group totals" });
    expect(within(table).getByRole("row", { name: "Design Changed 5 6" })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: "Sales Removed 2 —" })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: "Ops Added — 1" })).toBeInTheDocument();
    expect(within(table).queryByText("Same")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Restore previous version" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Saved versions changed");
    expect(request).toHaveBeenLastCalledWith("scene", "restoreTableOutput", ["result", history.revisionToken]);
    expect(screen.getByRole("button", { name: "Compare versions" })).toBeEnabled();
  });

  it("compares Markdown even when the source was removed", async () => {
    const { request, store } = makeStore();
    request.mockResolvedValueOnce({
      revisionToken: history.revisionToken,
      previous: {version: 1, sourceHash: "a".repeat(64), markdown: "Title\nOld"},
      current: {version: 2, sourceHash: "b".repeat(64), markdown: "Title\nNew"},
    });
    render(<TableOutput node={{...output, tableInputSourceId: "", tableInputStatus: "unbound"}} store={store} />);
    expect(screen.getByRole("button", {name: "Rerun from table"})).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Source removed");
    fireEvent.click(screen.getByRole("button", { name: "Compare versions" }));
    expect(await screen.findByText("Old")).toBeVisible();
    expect(screen.getByText("New")).toBeVisible();
    expect(screen.getByRole("button", { name: "Restore previous version" })).toBeEnabled();
  });

  it("protects unsaved edits, manual revisions and proposals", () => {
    const { store } = makeStore();
    const view = render(<TableOutput node={output} store={store} editing />);
    const rerun = screen.getByRole("button", {name: "Rerun from table"});
    expect(rerun).toBeDisabled();
    view.rerender(<TableOutput node={{...output, tableOutputEdited: true}} store={store} />);
    expect(rerun).toBeDisabled();
    view.rerender(<TableOutput node={{...output, artifactProposalId: "candidate"}} store={store} />);
    expect(rerun).toBeDisabled();
    view.rerender(<TableOutput node={output} store={store} />);
    expect(rerun).toBeEnabled();
  });
});
