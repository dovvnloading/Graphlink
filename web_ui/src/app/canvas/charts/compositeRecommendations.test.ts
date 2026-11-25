import { describe, expect, it } from "vitest";
import { canonicalizeChartSpec } from "../chartSpec";
import { recommendCompositeDesigns } from "./compositeRecommendations";
import { withCompositeDesign } from "./compositeDesign";

describe("deterministic composite figure advice", () => {
  it("recommends valid reported bounds only when they exist and do not cross", () => {
    const raw = { type: "composite", version: 2, composite: {
      numbers: { time: [0, 1, 2, 3], signal: [1, 2, 3, 5],
        low: [0.5, 1.5, 2.5, 4.5], high: [1.5, 2.5, 3.5, 5.5] },
      categories: { group: ["A", "A", "B", "B"] },
      layers: [{ mark: "point", x: "time", y: "signal" }],
    } };
    const chart = canonicalizeChartSpec(raw);
    const original = JSON.stringify(chart);
    const advice = recommendCompositeDesigns(chart);
    expect(advice[0].id).toBe("reported-interval");
    expect(advice[0].design.layers[0]).toMatchObject({
      mark: "band", y: "low", y2: "high",
    });
    expect(advice[0].design.facetBy).toBe("group");
    expect(withCompositeDesign(chart, advice[0].design).composite?.layers).toHaveLength(3);
    expect(JSON.stringify(chart)).toBe(original);
  });

  it("never recommends invented uncertainty bounds when columns cross", () => {
    const chart = canonicalizeChartSpec({ type: "composite", version: 2,
      composite: { numbers: { time: [0, 1, 2], signal: [2, 3, 4],
        low: [0, 2, 10], high: [1, 3, 5] },
        layers: [{ mark: "point", x: "time", y: "signal" }] },
    });
    const advice = recommendCompositeDesigns(chart);
    expect(advice.some(item => item.id === "reported-interval")).toBe(false);
    expect(advice[0].id).toBe("trend");
  });

  it("avoids connecting nonmonotonic X observations and excessive color categories", () => {
    const values = Array.from({ length: 20 }, (_, i) => i);
    const chart = canonicalizeChartSpec({ type: "composite", version: 2, composite: {
      numbers: { exposure: values.map(i => i % 4), response: values },
      categories: { group: values.map(i => "G" + i) },
      layers: [{ mark: "point", x: "exposure", y: "response" }],
    } });
    const advice = recommendCompositeDesigns(chart);
    expect(advice).toHaveLength(1);
    expect(advice[0].id).toBe("scatter");
    expect(advice[0].design.layers[0].colorBy).toBeUndefined();
  });
});
