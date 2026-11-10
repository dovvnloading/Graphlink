import { describe, expect, it } from "vitest";
import { displayGitlinkPath, splitGitlinkPreview } from "./gitlinkPreview";

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

  it("does not transform HTML-like diff content; rendering uses plain preformatted text", () => {
    const parsed = splitGitlinkPreview("### src/a.py [update]\n\n+<img src=x onerror=alert(1)>", [files[0]]);
    expect(parsed?.[0]).toContain("<img");
  });

  it("escapes paths consistently with backend headings and retains the full byte metadata", () => {
    const path = "line\nname\u202e😀.txt";
    const shown = '"line\\nname\\u202e\\ud83d\\ude00.txt"';
    expect(displayGitlinkPath(path)).toBe(shown);
    expect(displayGitlinkPath("src/normal.py")).toBe("src/normal.py");
    const bytePreview = [
      "### " + shown + " [update]", "",
      "Bytes changed: 3 -> 2 bytes.",
      "Line endings: CRLF=1, LF=0, CR=0 -> CRLF=0, LF=1, CR=0.",
      "Final newline: CRLF -> LF.",
      '-"x\\r\\n"', '+"x\\n"',
    ].join("\n");
    expect(splitGitlinkPreview(bytePreview, [{ path, operation: "update" }])).toEqual([bytePreview]);
  });
});
