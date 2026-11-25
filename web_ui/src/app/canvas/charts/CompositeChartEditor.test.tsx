import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { canonicalizeChartSpec } from "../chartSpec";
import { CompositeChartEditor } from "./CompositeChartEditor";
import { compositeDesignOf, withCompositeDesign } from "./compositeDesign";

const SOURCE = canonicalizeChartSpec({ type: "composite", version: 2,
  composite: {
    numbers: { time: [0, 1, 2, 3], signal: [1, 2, 3, 4], lower: [0, 1, 2, 3], upper: [2, 3, 4, 5] },
    categories: { treatment: ["a", "a", "b", "b"] },
    layers: [{ mark: "line", x: "time", y: "signal" }],
  },
});

describe("composite native editor", () => {
  it("previews and saves new mark layers without altering the source observations", async () => {
    const onPreview = vi.fn(), onSave = vi.fn().mockResolvedValue(undefined), onClose = vi.fn();
    const input = JSON.stringify(SOURCE);
    render(<CompositeChartEditor chartData={SOURCE}
      onPreview={onPreview} onSave={onSave} onClose={onClose} />);
    fireEvent.change(screen.getByLabelText("Facet panels"), { target: { value: "treatment" } });
    fireEvent.click(screen.getByRole("button", { name: "Add layer" }));
    expect(screen.getByLabelText("Mark 2")).toHaveValue("point");
    fireEvent.change(screen.getByLabelText("Mark 2"), { target: { value: "line" } });
    expect(onPreview).toHaveBeenCalled();
    const latest = onPreview.mock.lastCall?.[0];
    expect(latest.composite.layers).toHaveLength(2);
    expect(latest.composite.facetBy).toBe("treatment");
    expect(latest.composite.numbers).toEqual(SOURCE.composite?.numbers);
    fireEvent.click(screen.getByRole("button", { name: "Save figure design" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
    expect(onSave.mock.calls[0][0].layers).toHaveLength(2);
    expect(onSave.mock.calls[0][1]).toEqual(compositeDesignOf(SOURCE));
    expect(onClose).toHaveBeenCalledOnce();
    expect(JSON.stringify(SOURCE)).toBe(input);
  });

  it("refuses unsupported layer references or invalid bounds", () => {
    const old = compositeDesignOf(SOURCE);
    expect(() => withCompositeDesign(SOURCE, {
      layers: [{ mark: "line", x: "time", y: "unknown" }], facetBy: null,
    })).toThrow();
    expect(() => withCompositeDesign(SOURCE, {
      layers: [{ mark: "band", x: "time", y: "upper", y2: "lower" }], facetBy: null,
    })).toThrow();
    expect(SOURCE.composite?.layers).toEqual(old.layers);
  });

  it("shows invalid band previews and blocks save until a valid column is selected", () => {
    const onPreview = vi.fn(), onSave = vi.fn(), onClose = vi.fn();
    render(<CompositeChartEditor chartData={SOURCE}
      onPreview={onPreview} onSave={onSave} onClose={onClose} />);
    fireEvent.change(screen.getByLabelText("Mark 1"), { target: { value: "band" } });
    expect(screen.getByRole("alert")).toHaveTextContent(/lower bounds must not exceed upper/);
    expect(screen.getByRole("button", { name: "Save figure design" })).toBeDisabled();
    expect(onPreview).toHaveBeenLastCalledWith(null);
    fireEvent.change(screen.getByLabelText("Upper bound 1"), { target: { value: "upper" } });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("button", { name: "Save figure design" })).not.toBeDisabled();
  });

  it("resets local preview on cancel and does not submit anything", () => {
    const onPreview = vi.fn(), onSave = vi.fn(), onClose = vi.fn();
    render(<CompositeChartEditor chartData={SOURCE}
      onPreview={onPreview} onSave={onSave} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(onSave).not.toHaveBeenCalled();
  });
});
