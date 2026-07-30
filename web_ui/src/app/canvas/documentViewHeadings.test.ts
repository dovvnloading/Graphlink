import { describe, expect, it } from "vitest";
import { extractHeadings } from "./documentViewHeadings";

describe("extractHeadings", () => {
  it("extracts depth, text, and a slug id for each heading", () => {
    const headings = extractHeadings("# Title\n\n## Section One\n\n### Subsection");
    expect(headings).toEqual([
      { depth: 1, text: "Title", id: "title" },
      { depth: 2, text: "Section One", id: "section-one" },
      { depth: 3, text: "Subsection", id: "subsection" },
    ]);
  });

  it("flattens inline formatting within a heading's own text", () => {
    const headings = extractHeadings("## **Bold** and *italic* heading");
    expect(headings).toEqual([{ depth: 2, text: "Bold and italic heading", id: "bold-and-italic-heading" }]);
  });

  it("de-duplicates identical heading text with a numeric suffix, matching github-slugger's own behavior", () => {
    const headings = extractHeadings("## Summary\n\nfirst\n\n## Summary\n\nsecond");
    expect(headings.map((h) => h.id)).toEqual(["summary", "summary-1"]);
  });

  it("ignores non-heading content (paragraphs, lists, code blocks)", () => {
    const headings = extractHeadings("# Real Heading\n\nA paragraph.\n\n- item one\n- item two\n\n```js\n// not a heading\n```");
    expect(headings).toEqual([{ depth: 1, text: "Real Heading", id: "real-heading" }]);
  });

  it("returns an empty array for content with no headings", () => {
    expect(extractHeadings("Just a plain paragraph, nothing else.")).toEqual([]);
  });

  it("returns an empty array for empty content", () => {
    expect(extractHeadings("")).toEqual([]);
  });

  it("matches the conversation-transcript formatter's own numbered heading shape without collisions", () => {
    // Mirrors SceneCanvas.tsx's conversationHistoryToDocumentMarkdown output
    // shape exactly (`### {index+1}. {role}`) - every heading is unique by
    // construction (the leading number), so no de-dup suffix should ever
    // appear here.
    const transcript = "## Conversation Transcript\n\n### 1. User\n\nhi\n\n### 2. Assistant\n\nhello";
    const headings = extractHeadings(transcript);
    expect(headings.map((h) => h.id)).toEqual(["conversation-transcript", "1-user", "2-assistant"]);
  });

  it("omits a heading that is only an image, rather than fabricating a dead-link entry", () => {
    // Caught by adversarial review: mdast-util-to-string special-cases
    // `image` nodes and returns their alt text by default, but the actual
    // rendered heading (flattened via hast-util-to-string inside
    // rehype-slug) treats an <img> as a childless leaf and contributes
    // nothing - so this heading renders with an empty id. Extracting
    // "Alt Text" here would produce a ToC entry that scrolls to nothing;
    // the existing `!text` guard now correctly drops it instead, since
    // includeImageAlt:false makes its extracted text empty too.
    expect(extractHeadings("## ![Alt Text](img.png)")).toEqual([]);
  });

  it("excludes image alt text from a heading's extracted text and id when mixed with real text", () => {
    // Same bug as above, but with surrounding text so the heading isn't
    // dropped entirely - the id must match what rehype-slug assigns the
    // real rendered heading (whose <img> also contributes nothing).
    const headings = extractHeadings("## Some ![alt text](img.png) Heading");
    expect(headings).toEqual([{ depth: 2, text: "Some  Heading", id: "some--heading" }]);
  });

  it("strips GFM strikethrough syntax from the extracted text, matching the rendered heading", () => {
    // Caught by adversarial review: without remark-gfm, the extraction
    // parser doesn't understand `~~...~~` as strikethrough, so it would
    // leak the literal tildes into the ToC label while the real pipeline
    // (which does use remark-gfm) renders it struck-through with no tildes.
    const headings = extractHeadings("## Hello ~~World~~");
    expect(headings).toEqual([{ depth: 2, text: "Hello World", id: "hello-world" }]);
  });
});
