import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ChartDataRow } from "../../../lib/bridge-core/generated/scene-state";
import ChartOverview from "./ChartOverview";

describe("chart overview", () => {
  const charts: [ChartDataRow, string, number][] = [
    [{ type: "line", labels: ["A", "B"], values: [1, 20] }, "polyline", 1],
    [{ type: "bar", labels: ["A", "B"], values: [-4, 20] }, "rect", 2],
    [{ type: "histogram", values: [1, 1, 2, 4], bins: 2 }, "rect", 2],
    [{ type: "pie", labels: ["A", "B"], values: [1, 3] }, "circle", 2],
    [{ type: "sankey", flows: [{ source: "A", target: "B", value: 12 }] }, "rect", 2],
  ];

  it.each(charts)("draws the saved %j as a passive chart", (chartData, selector, count) => {
    render(<ChartOverview chartData={{ ...chartData, title: "Overview" }} chartType={chartData.type!} />);
    const image = screen.getByRole("img", { name: `Overview (${chartData.type} chart)` });
    expect(image.querySelectorAll(selector)).toHaveLength(count);
    expect(image.innerHTML).not.toMatch(/NaN|Infinity/);
    expect(image).not.toHaveClass("nowheel");
  });

  it("shows a reason for an unreadable saved chart in overview", () => {
    render(<ChartOverview chartType="bar" chartData={{ type: "bar" }} />);
    expect(screen.getByText("Labels and values must both be lists")).toBeInTheDocument();
  });
});
