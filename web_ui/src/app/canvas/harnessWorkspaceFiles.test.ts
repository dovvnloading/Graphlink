import { describe, expect, it } from "vitest";
import {
  harnessFileDocumentMarkdown,
  parseHarnessWorkspaceDocument,
  parseHarnessWorkspaceListing,
} from "./harnessWorkspaceFiles";

describe("workspace file response validation", () => {
  it("validates a folder listing and its entries", () => {
    const value = {
      root: "/tmp/work", isScratch: false, path: "docs", truncated: false,
      entries: [{ name: "readme.md", path: "docs/readme.md", isDir: false, size: 10 }],
    };
    expect(parseHarnessWorkspaceListing(value)).toEqual(value);
    expect(() => parseHarnessWorkspaceListing({ ...value, entries: [{ name: "file", path: 42 }] }))
      .toThrow("Invalid workspace file entry");
    expect(() => parseHarnessWorkspaceListing(null)).toThrow("Invalid workspace folder response");
    expect(parseHarnessWorkspaceListing({ ...value, nextCursor: "page-two" }).nextCursor).toBe("page-two");
    expect(() => parseHarnessWorkspaceListing({ ...value, nextCursor: 7 }))
      .toThrow("Invalid workspace folder response");
  });

  it("validates document preview payloads", () => {
    expect(parseHarnessWorkspaceDocument({ path: "notes.txt", content: "hello", truncated: false }))
      .toEqual({ path: "notes.txt", content: "hello", truncated: false });
    expect(() => parseHarnessWorkspaceDocument({ path: "x", content: 123, truncated: false }))
      .toThrow("Invalid workspace document");
  });
});

describe("Document View workspace previews", () => {
  it("renders markdown directly", () => {
    expect(harnessFileDocumentMarkdown({ path: "docs/readme.md", content: "# Hello", truncated: false }))
      .toBe("# Hello");
  });

  it("fences non-markdown text without allowing embedded fences to close it", () => {
    const text = "example with ``` code fence";
    const formatted = harnessFileDocumentMarkdown({ path: "src/example.py", content: text, truncated: false });
    expect(formatted.startsWith("````\n")).toBe(true);
    expect(formatted).toContain(text);
    expect(formatted.endsWith("\n````")).toBe(true);
  });

  it("labels bounded previews and empty files", () => {
    expect(harnessFileDocumentMarkdown({ path: "log.txt", content: "", truncated: true }))
      .toContain("Preview truncated at 40,000 characters.");
    expect(harnessFileDocumentMarkdown({ path: "log.txt", content: "", truncated: false }))
      .toContain("(Empty file)");
  });
});
