import { describe, expect, it } from "vitest";
import { reviewDiffContext } from "./reviewDiffFocus";

const DIFF = [
  "diff --git a/a.py b/a.py",
  "index 123..456 100644",
  "--- a/a.py",
  "+++ b/a.py",
  "@@ -12,3 +12,4 @@",
  " before",
  "-old",
  "+new",
  "+other",
  " after",
  "diff --git a/b.py b/b.py",
  "--- a/b.py",
  "+++ b/b.py",
  "@@ -0,0 +1 @@",
  "+hello",
].join("\n");

describe("reviewDiffContext", () => {
  it("shows the right file/hunk around the new-side line, without other files", () => {
    const result = reviewDiffContext(DIFF, "a.py", 13);
    expect(result).toContain("@@ -12,3 +12,4 @@");
    expect(result).toContain("+new");
    expect(result).toContain("-old");
    expect(result).not.toContain("b/b.py");
    expect(result).not.toContain("+hello");
  });

  it("finds a later file and handles missing, deleted or old-side line numbers", () => {
    expect(reviewDiffContext(DIFF, "b.py", 1)).toContain("+hello");
    expect(reviewDiffContext(DIFF, "a.py", 999)).toBeNull();
    expect(reviewDiffContext(DIFF, "unknown.py", 1)).toBeNull();
    expect(reviewDiffContext(DIFF, "a.py", 0)).toContain("diff --git a/a.py");
    expect(reviewDiffContext("", "a.py", 1)).toBeNull();
  });

  it("does not match prefix collisions such as util.py vs test_util.py", () => {
    expect(reviewDiffContext(DIFF, "py", 1)).toBeNull();
    expect(reviewDiffContext(DIFF, "b.py", 1)).not.toContain("a/a.py");
  });

  it("does not run code or parse HTML from the diff", () => {
    const hostile = "diff --git a/html.py b/html.py\n--- a/html.py\n+++ b/html.py\n@@ -0,0 +1 @@\n+<script>alert(1)</script>";
    expect(reviewDiffContext(hostile, "html.py", 1)).toContain("<script>alert(1)</script>");
  });
});
