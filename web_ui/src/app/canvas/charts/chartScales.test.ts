import { describe, expect, it } from "vitest";
import {
  computeHistogramBins,
  createLinearScale,
  formatValue,
  mean,
  median,
  niceTicks,
  pickCategoricalTicks,
  wrapLabel,
} from "./chartScales";

describe("createLinearScale", () => {
  it("maps the domain endpoints to the range endpoints", () => {
    const scale = createLinearScale([0, 100], [200, 0]);
    expect(scale(0)).toBe(200);
    expect(scale(100)).toBe(0);
    expect(scale(50)).toBe(100);
  });

  it("extrapolates linearly for values outside the domain", () => {
    const scale = createLinearScale([0, 10], [0, 100]);
    expect(scale(-5)).toBe(-50);
    expect(scale(15)).toBe(150);
  });

  it("returns the range midpoint for a zero-width domain rather than dividing by zero", () => {
    const scale = createLinearScale([5, 5], [0, 100]);
    expect(scale(5)).toBe(50);
  });
});

describe("niceTicks", () => {
  it("produces round-number ticks spanning the requested range", () => {
    const ticks = niceTicks(0, 97, 5);
    expect(ticks[0]).toBeLessThanOrEqual(0);
    expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(97);
    for (let i = 1; i < ticks.length; i += 1) {
      expect(ticks[i] - ticks[i - 1]).toBeCloseTo(ticks[1] - ticks[0]);
    }
  });

  it("handles a degenerate single-value domain without crashing", () => {
    expect(niceTicks(5, 5)).toEqual([4, 5, 6]);
    expect(niceTicks(0, 0)).toEqual([0]);
  });

  it("handles negative domains", () => {
    const ticks = niceTicks(-50, -10, 4);
    expect(ticks[0]).toBeLessThanOrEqual(-50);
    expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(-10);
  });
});

describe("formatValue", () => {
  it("thousands-groups large numbers with no decimals", () => {
    expect(formatValue(12345)).toBe("12,345");
  });

  it("snaps near-integers to whole numbers", () => {
    expect(formatValue(4.0004)).toBe("4");
  });

  it("keeps two decimals for genuine fractions, trimming trailing zeros", () => {
    expect(formatValue(4.5)).toBe("4.5");
    expect(formatValue(4.25)).toBe("4.25");
  });
});

describe("wrapLabel", () => {
  it("returns a single line when the text fits", () => {
    expect(wrapLabel("Q1", 14, 2)).toEqual(["Q1"]);
  });

  it("wraps onto multiple lines at word boundaries", () => {
    const lines = wrapLabel("North America Region", 10, 2);
    expect(lines.length).toBe(2);
    expect(lines.join(" ")).toContain("North");
  });

  it("truncates with an ellipsis when content remains after maxLines", () => {
    const lines = wrapLabel("one two three four five six seven eight", 6, 2);
    expect(lines.length).toBe(2);
    expect(lines[1].endsWith("...")).toBe(true);
  });

  it("returns an empty array for blank input", () => {
    expect(wrapLabel("   ")).toEqual([]);
  });
});

describe("pickCategoricalTicks", () => {
  it("keeps every index when they all fit", () => {
    const { indexes, rotate } = pickCategoricalTicks(3, 400);
    expect(indexes).toEqual([0, 1, 2]);
    expect(rotate).toBe(false);
  });

  it("thins a large label count down to a readable stride and always keeps the last index", () => {
    const { indexes, rotate } = pickCategoricalTicks(50, 360);
    expect(indexes[indexes.length - 1]).toBe(49);
    expect(indexes.length).toBeLessThan(50);
    expect(rotate).toBe(true);
  });

  it("returns nothing for zero categories", () => {
    expect(pickCategoricalTicks(0, 400)).toEqual({ indexes: [], rotate: false });
  });
});

describe("computeHistogramBins", () => {
  it("splits the value range into equal-width bins and counts membership", () => {
    const bins = computeHistogramBins([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], 5);
    expect(bins).toHaveLength(5);
    expect(bins.reduce((sum, bin) => sum + bin.count, 0)).toBe(10);
    expect(bins[0].x0).toBe(0);
    expect(bins[bins.length - 1].x1).toBe(9);
  });

  it("puts a value exactly equal to the max into the last bin, not an overflow bin", () => {
    const bins = computeHistogramBins([0, 10], 2);
    expect(bins).toHaveLength(2);
    expect(bins[0].count + bins[1].count).toBe(2);
    expect(bins[1].count).toBeGreaterThanOrEqual(1);
  });
});

describe("mean/median", () => {
  it("computes the arithmetic mean", () => {
    expect(mean([1, 2, 3, 4])).toBe(2.5);
  });

  it("computes the median for both odd and even-length inputs", () => {
    expect(median([1, 3, 2])).toBe(2);
    expect(median([1, 2, 3, 4])).toBe(2.5);
  });
});
