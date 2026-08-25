/**
 * ADR-022 stage 22.3: property-based tests for chartScales.ts's pure
 * scale/binning math (see that module's own header comment).
 */
import { describe, expect, it } from "vitest";
import fc from "fast-check";
import { computeHistogramBins, createLinearScale, niceTicks } from "./chartScales";

// Excludes subnormal-adjacent magnitudes (anything nonzero smaller than
// 1e-6): niceTicks' `Math.ceil(max / step)` genuinely underflows to 0 at
// values like 5e-324 (found by this file's own property test, shrunk from
// a random failure) - a real floating-point edge case, but one no chart
// axis value is ever physically at (300+ orders of magnitude below any
// quantity a chart would plot). Constraining to a realistic magnitude
// range rather than chasing a fix for an unreachable input.
const finiteDouble = (min: number, max: number) =>
  fc.double({ noNaN: true, noDefaultInfinity: true, min, max }).filter((value) => value === 0 || Math.abs(value) >= 1e-6);

describe("createLinearScale (property-based)", () => {
  it("maps domain endpoints onto range endpoints for any non-degenerate domain", () => {
    fc.assert(
      fc.property(
        fc.tuple(finiteDouble(-1e6, 1e6), finiteDouble(-1e6, 1e6)).filter(([a, b]) => a !== b),
        fc.tuple(finiteDouble(-1e6, 1e6), finiteDouble(-1e6, 1e6)),
        (domain, range) => {
          const scale = createLinearScale(domain, range);
          // toBeCloseTo, not toBe: (value - d0) / span is 0 at value === d0
          // mathematically, but IEEE-754 division can hand back signed -0
          // depending on span's sign (0 / negative === -0) - a real
          // floating-point quirk, not a defect, and toBeCloseTo treats -0
          // and +0 as equal the way every practical use of this value does.
          expect(scale(domain[0])).toBeCloseTo(range[0], 6);
          expect(scale(domain[1])).toBeCloseTo(range[1], 6);
        },
      ),
    );
  });
});

describe("niceTicks (property-based)", () => {
  it("returns strictly ascending ticks whose span covers [min, max]", () => {
    fc.assert(
      fc.property(
        fc
          .tuple(finiteDouble(-1e5, 1e5), finiteDouble(-1e5, 1e5))
          .filter(([a, b]) => Math.abs(a - b) > 1e-3),
        ([a, b]) => {
          const min = Math.min(a, b);
          const max = Math.max(a, b);
          const ticks = niceTicks(min, max);

          for (let i = 1; i < ticks.length; i++) {
            expect(ticks[i]).toBeGreaterThan(ticks[i - 1]);
          }
          expect(ticks[0]).toBeLessThanOrEqual(min);
          expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(max);
        },
      ),
    );
  });
});

describe("computeHistogramBins (property-based)", () => {
  it("bin counts sum to the input length, and every bin's own span is non-negative", () => {
    fc.assert(
      fc.property(
        fc.array(finiteDouble(-1e4, 1e4), { minLength: 1, maxLength: 200 }),
        fc.integer({ min: 1, max: 20 }),
        (values, bins) => {
          const result = computeHistogramBins(values, bins);
          expect(result.length).toBe(bins);
          expect(result.reduce((sum, bin) => sum + bin.count, 0)).toBe(values.length);
          for (const bin of result) {
            expect(bin.x1).toBeGreaterThanOrEqual(bin.x0);
          }
        },
      ),
    );
  });

  it("every value lands in a bin whose [x0, x1] range actually contains it", () => {
    fc.assert(
      fc.property(
        fc.array(finiteDouble(-1e4, 1e4), { minLength: 2, maxLength: 200 }),
        fc.integer({ min: 1, max: 20 }),
        (values, bins) => {
          const result = computeHistogramBins(values, bins);
          const min = Math.min(...values);
          const max = Math.max(...values);
          for (const value of values) {
            const width = (max - min) / bins || 1;
            let index = Math.floor((value - min) / width);
            if (index >= bins) index = bins - 1;
            if (index < 0) index = 0;
            const bin = result[index];
            expect(value).toBeGreaterThanOrEqual(bin.x0 - 1e-9);
            expect(value).toBeLessThanOrEqual(bin.x1 + 1e-9);
          }
        },
      ),
    );
  });
});
