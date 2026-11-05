import { describe, expect, it } from "vitest";
import { splitGitlinkPreview } from "./gitlinkPreview";

const files = [
  { path: "src/a.py", operation: "update" },
  { path: "src/b.py", operation: "create" },
];
const preview = [
  "### src/a.py [update]",
  "",
  "--- a/src/a.py",
  "+++ b/src/a.py",
  "@@ -1 +1 @@",
  "-old",
  "+new",
  "",
  "### src/b.py [create]",
  "",
  "--- a/src/b.py",
  "+++ b/src/b.py",
  "@@ -0,0 +1 @@",
  "+created",
].join("\n");

describe("splitGitlinkPreview", () => {
  it("separates the approved files and retains their own diff content", () => {
    const parsed = splitGitlinkPreview(preview, files);
    expect(parsed).not.toBeNull();
    expect(parsed![0]).toContain("-old");
    expect(parsed![0]).not.toContain("+created");
    expect(parsed![1]).toContain("+created");
    expect(parsed![1]).not.toContain("-old");
  });

  it("fails closed to the full diff for unknown or incomplete formats", () => {
    expect(splitGitlinkPreview("raw legacy diff", files)).toBeNull();
    expect(splitGitlinkPreview(preview, [])).toBeNull();
    expect(splitGitlinkPreview("", files)).toBeNull();
    expect(splitGitlinkPreview(preview, [...files, { path: "missing.py", operation: "delete" }]))
      .toBeNull();
  });

  it("does not transform HTML-like diff content; rendering uses a code fence", () => {
    const parsed = splitGitlinkPreview("### src/a.py [update]\n\n+<img src=x onerror=alert(1)>", [files[0]]);
    expect(parsed?.[0]).toContain("<img");
  });
});
