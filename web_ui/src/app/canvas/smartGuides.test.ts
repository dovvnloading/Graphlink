import { describe, expect, it } from "vitest";
import { ALIGNMENT_TOLERANCE_PX, computeSmartGuideSnap, type Rect } from "./smartGuides";

// Direct unit coverage of the legacy _calculate_smart_guide_snap port - every
// documented semantic (same-key matching, key order, first-candidate-wins,
// strict tolerance, per-axis independence, early exit) gets its own test so a
// regression in any one rule fails loudly by name.

function rect(x: number, y: number, width = 100, height = 50): Rect {
  return { x, y, width, height };
}

describe("computeSmartGuideSnap", () => {
  it("exports legacy's exact ALIGNMENT_TOLERANCE of 5px as the default", () => {
    expect(ALIGNMENT_TOLERANCE_PX).toBe(5);
  });

  it("returns the position unchanged with no candidates", () => {
    const result = computeSmartGuideSnap(rect(203, 400), []);
    expect(result).toEqual({ x: 203, y: 400, guides: [] });
  });

  it("returns the position unchanged when nothing aligns", () => {
    const result = computeSmartGuideSnap(rect(0, 0), [rect(500, 500)]);
    expect(result).toEqual({ x: 0, y: 0, guides: [] });
  });

  it("snaps left-to-left and draws a vertical guide spanning the union of both rects", () => {
    const result = computeSmartGuideSnap(rect(203, 400), [rect(200, 100, 80, 40)]);
    expect(result.x).toBe(200);
    expect(result.y).toBe(400);
    expect(result.guides).toEqual([
      // position = the candidate's key value; span = min(tops)..max(bottoms).
      { orientation: "vertical", position: 200, start: 100, end: 450 },
    ]);
  });

  it("snaps center-to-center, back-solving x from the moving rect's half-width", () => {
    // moving center = 0 + 100/2 = 50; candidate center = 30 + 44/2 = 52.
    const result = computeSmartGuideSnap(rect(0, 400), [rect(30, 100, 44, 40)]);
    expect(result.x).toBe(2); // 52 - 100/2
  });

  it("snaps right-to-right, back-solving x from the moving rect's full width", () => {
    // moving right = 100; candidate right = 52 + 50 = 102.
    const result = computeSmartGuideSnap(rect(0, 400), [rect(52, 100, 50, 40)]);
    expect(result.x).toBe(2); // 102 - 100
  });

  it("snaps top-to-top and draws a horizontal guide", () => {
    const result = computeSmartGuideSnap(rect(400, 103), [rect(100, 100, 80, 40)]);
    expect(result.y).toBe(100);
    expect(result.guides).toEqual([
      { orientation: "horizontal", position: 100, start: 100, end: 500 },
    ]);
  });

  it("snaps middle-to-middle, back-solving y from the moving rect's half-height", () => {
    // moving middle = 25; candidate middle = -100 + 254/2 = 27; candidate's
    // top (-100) and bottom (154) are both far outside tolerance, isolating
    // the middle key.
    const result = computeSmartGuideSnap(rect(400, 0), [rect(100, -100, 80, 254)]);
    expect(result.y).toBe(2); // 27 - 50/2
  });

  it("snaps bottom-to-bottom, back-solving y from the moving rect's full height", () => {
    // moving bottom = 50; candidate bottom = -200 + 252 = 52.
    const result = computeSmartGuideSnap(rect(400, 0), [rect(100, -200, 80, 252)]);
    expect(result.y).toBe(2); // 52 - 50
  });

  it("uses a STRICT less-than tolerance (a 5px offset does not snap, 4.9px does)", () => {
    expect(computeSmartGuideSnap(rect(205, 400), [rect(200, 100)]).x).toBe(205);
    expect(computeSmartGuideSnap(rect(204.9, 400), [rect(200, 100)]).x).toBe(200);
  });

  it("tries keys in left/center/right order and takes the FIRST hit, not the closest", () => {
    // moving left = 203 (3px from candidate left 200); moving center = 253
    // (1px from candidate center 252 with candidate width 104). Center is
    // closer, but left is tested first - legacy took the first hit.
    const result = computeSmartGuideSnap(rect(203, 400), [rect(200, 100, 104, 40)]);
    expect(result.x).toBe(200);
  });

  it("scans candidates in array order and takes the first aligning one", () => {
    const result = computeSmartGuideSnap(rect(203, 400), [
      rect(200, 100),
      rect(201, 200),
    ]);
    expect(result.x).toBe(200);
    expect(result.guides).toHaveLength(1);
    expect(result.guides[0].position).toBe(200);
  });

  it("can snap X and Y against two DIFFERENT candidates in one call", () => {
    const result = computeSmartGuideSnap(rect(203, 402), [
      rect(200, 900), // aligns X only (left 200 vs 203)
      rect(900, 400), // aligns Y only (top 400 vs 402)
    ]);
    expect(result.x).toBe(200);
    expect(result.y).toBe(400);
    expect(result.guides).toHaveLength(2);
    expect(result.guides.map((g) => g.orientation).sort()).toEqual(["horizontal", "vertical"]);
  });

  it("stops scanning once both axes are snapped - at most one guide per axis", () => {
    const result = computeSmartGuideSnap(rect(203, 402), [
      rect(200, 400), // aligns both axes at once
      rect(201, 401), // would also align both - must contribute nothing
    ]);
    expect(result.x).toBe(200);
    expect(result.y).toBe(400);
    expect(result.guides).toHaveLength(2);
    expect(result.guides.every((g) => g.position === 200 || g.position === 400)).toBe(true);
  });

  it("an already-snapped axis is not re-tested against later candidates", () => {
    // First candidate snaps X; second would snap X differently but only its
    // Y alignment may land.
    const result = computeSmartGuideSnap(rect(203, 402), [
      rect(200, 900), // X -> 200
      rect(204, 400), // X would -> 204, but X is taken; Y -> 400
    ]);
    expect(result.x).toBe(200);
    expect(result.y).toBe(400);
  });
});
