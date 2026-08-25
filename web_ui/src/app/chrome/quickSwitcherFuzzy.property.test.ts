/**
 * ADR-022 stage 22.3: property-based tests for quickSwitcherFuzzy.ts's
 * subsequence-matching contract (see that module's own header comment).
 */
import { describe, expect, it } from "vitest";
import fc from "fast-check";
import { fuzzyFilterAndSort, fuzzyScore } from "./quickSwitcherFuzzy";

describe("fuzzyScore (property-based)", () => {
  it("a non-null score implies query is an in-order, case-insensitive subsequence of target", () => {
    fc.assert(
      fc.property(fc.string(), fc.string(), (query, target) => {
        const score = fuzzyScore(query, target);
        if (score === null) return;

        const q = query.toLowerCase();
        const t = target.toLowerCase();
        let qi = 0;
        for (let ti = 0; ti < t.length && qi < q.length; ti++) {
          if (t[ti] === q[qi]) qi++;
        }
        expect(qi).toBe(q.length);
      }),
    );
  });

  it("an empty query always scores 0, matching every target", () => {
    fc.assert(
      fc.property(fc.string(), (target) => {
        expect(fuzzyScore("", target)).toBe(0);
      }),
    );
  });
});

describe("fuzzyFilterAndSort (property-based)", () => {
  it("keeps exactly the non-null-scoring items, sorted ascending by score", () => {
    fc.assert(
      fc.property(fc.string(), fc.array(fc.string()), (query, items) => {
        const trimmed = query.trim();
        const result = fuzzyFilterAndSort(trimmed, items, (item) => item);

        const expectedCount = items.filter((item) => fuzzyScore(trimmed, item) !== null).length;
        expect(result.length).toBe(expectedCount);

        const scores = result.map((item) => fuzzyScore(trimmed, item) as number);
        for (let i = 1; i < scores.length; i++) {
          expect(scores[i]).toBeGreaterThanOrEqual(scores[i - 1]);
        }
      }),
    );
  });
});
