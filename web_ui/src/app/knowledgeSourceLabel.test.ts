import { describe, expect, it } from "vitest";
import { knowledgeSourceLabel } from "./knowledgeSourceLabel";

describe("knowledge source labels", () => {
  it("hides absolute local directories from Windows, POSIX and file URLs", () => {
    expect(knowledgeSourceLabel(String.raw`C:\Users\Ada\.graphlink\knowledge\uploads\id\0\lighthouse.md`))
      .toBe("lighthouse.md");
    expect(knowledgeSourceLabel("/home/ada/.graphlink/knowledge/uploads/id/0/lighthouse.md"))
      .toBe("lighthouse.md");
    expect(knowledgeSourceLabel("file:///home/ada/private/notes.md")).toBe("notes.md");
  });

  it("preserves website links and relative paths, and names chat sources", () => {
    expect(knowledgeSourceLabel("https://example.org/notes.md")).toBe("https://example.org/notes.md");
    expect(knowledgeSourceLabel("docs/recovery.md")).toBe("docs/recovery.md");
    expect(knowledgeSourceLabel("graph:27", "Saved chat")).toBe("Saved chat");
    expect(knowledgeSourceLabel("branch:abc", "Indexed branch")).toBe("Indexed branch");
  });
});
