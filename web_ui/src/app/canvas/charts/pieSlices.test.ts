import { describe, expect, it } from "vitest";
import { buildSlices, formatPieShare } from "./pieSlices";

describe("pie slices", () => {
  it.each([
    { values: [1e308, 1e308], angles: [180, 180], shares: ["50.0%", "50.0%"] },
    { values: Array<number>(12).fill(1e308), angles: [...Array<number>(9).fill(30), 90],
      shares: [...Array<string>(9).fill("8.3%"), "25.0%"] },
    { values: [1e308, 1e-308], angles: [360, 0], shares: ["100.0%", "<0.1%"] },
    { values: [Number.MIN_VALUE, Number.MIN_VALUE], angles: [180, 180], shares: ["50.0%", "50.0%"] },
    { values: [2, 4], angles: [120, 240], shares: ["33.3%", "66.7%"] },
  ])("preserves finite proportional geometry for $values", ({ values, angles, shares }) => {
    const original = [...values];
    const slices = buildSlices(values.map((_, index) => "L" + index), values);
    expect(slices).toHaveLength(angles.length);
    expect(slices[0].startAngle).toBe(0);
    expect(slices.at(-1)?.endAngle).toBe(360);
    slices.forEach((slice, index) => {
      expect(Number.isFinite(slice.startAngle)).toBe(true);
      expect(Number.isFinite(slice.endAngle)).toBe(true);
      expect(slice.endAngle).toBeGreaterThanOrEqual(slice.startAngle);
      expect(slice.endAngle - slice.startAngle).toBeCloseTo(angles[index], 10);
      if (index > 0) expect(slice.startAngle).toBe(slices[index - 1].endAngle);
      expect(formatPieShare(slice.share)).toBe(shares[index]);
    });
    expect(slices.reduce((sum, slice) => sum + slice.share, 0)).toBeCloseTo(1, 12);
    expect(values).toEqual(original);
  });

  it("keeps stable order and a meaningful Other share even if its raw value overflows", () => {
    const slices = buildSlices(Array.from({ length: 12 }, (_, i) => "L" + i), Array<number>(12).fill(1e308));
    expect(slices.map(slice => slice.label)).toEqual([
      "L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "Other",
    ]);
    expect(slices.at(-1)?.value).toBe(Infinity);
    expect(slices.at(-1)?.share).toBeCloseTo(0.25);
  });

  it("has no slices for an empty input", () => {
    expect(buildSlices([], [])).toEqual([]);
  });
});
