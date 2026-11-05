import { describe, expect, it } from "vitest";
import { artifactDiffPreview } from "./artifactProposalDiff";

describe("Artifact proposal change-region preview", () => {
  it("shows changed middle while omitting truly unchanged outer lines", () => {
    const diff = artifactDiffPreview("header\nold\nfooter", "header\nnew\nfooter");
    expect(diff.leadingUnchangedLines).toBe(1);
    expect(diff.trailingUnchangedLines).toBe(1);
    expect(diff.before).toBe("old");
    expect(diff.after).toBe("new");
    expect(diff.changed).toBe(true);
  });

  it("reports no change when the generated text is identical", () => {
    expect(artifactDiffPreview("same", "same")).toMatchObject({
      changed: false, before: "", after: "",
    });
  });

  it("does not pretend two nonadjacent changes form a minimal diff", () => {
    const diff = artifactDiffPreview("before\nkeep\nbefore", "after\nkeep\nafter");
    expect(diff.before).toBe("before\nkeep\nbefore");
    expect(diff.after).toBe("after\nkeep\nafter");
  });

  it("flags character clipping on a single very long line", () => {
    const diff = artifactDiffPreview("old", "X".repeat(6000));
    expect(diff.after.length).toBe(5000);
    expect(diff.clippedAfterChars).toBe(true);
    expect(diff.clippedBeforeChars).toBe(false);
  });

  it("bounds display rows without discarding the actual proposal", () => {
    const before = Array.from({ length: 100 }, (_, i) => `before-${i}`).join("\n");
    const after = Array.from({ length: 100 }, (_, i) => `after-${i}`).join("\n");
    const diff = artifactDiffPreview(before, after, 5);
    expect(diff.before.split("\n")).toHaveLength(5);
    expect(diff.after.split("\n")).toHaveLength(5);
    expect(diff.omittedBeforeLines).toBe(95);
    expect(diff.omittedAfterLines).toBe(95);
  });
});
