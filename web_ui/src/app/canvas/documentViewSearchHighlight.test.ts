import { describe, expect, it } from "vitest";
import { escapeSearchRegExp, MAX_DOCUMENT_SEARCH_MATCHES, rehypeHighlightSearchMatches } from "./documentViewSearchHighlight";

interface TextNode {
  type: "text";
  value: string;
}

interface ElementNode {
  type: "element";
  tagName: string;
  properties: Record<string, unknown>;
  children: unknown[];
}

function text(value: string): TextNode {
  return { type: "text", value };
}

function paragraph(...children: unknown[]): ElementNode {
  return { type: "element", tagName: "p", properties: {}, children };
}

function root(...children: unknown[]) {
  return { type: "root", children };
}

function marks(node: ElementNode): ElementNode[] {
  return node.children.filter(
    (child): child is ElementNode =>
      typeof child === "object" && child !== null && (child as ElementNode).tagName === "mark",
  );
}

function textContent(node: TextNode | ElementNode): string {
  return node.type === "text"
    ? node.value
    : node.children.map((child) => textContent(child as TextNode | ElementNode)).join("");
}

describe("escapeSearchRegExp", () => {
  it("escapes every regex metacharacter", () => {
    expect(escapeSearchRegExp("a.b*c+d?e^f$g{h}i(j)k|l[m]n\\o")).toBe(
      "a\\.b\\*c\\+d\\?e\\^f\\$g\\{h\\}i\\(j\\)k\\|l\\[m\\]n\\\\o",
    );
  });

  it("leaves plain alphanumeric text untouched", () => {
    expect(escapeSearchRegExp("hello world 123")).toBe("hello world 123");
  });
});

describe("rehypeHighlightSearchMatches", () => {
  it("does nothing for an empty or whitespace-only query", () => {
    const tree = root(paragraph(text("hello world")));
    rehypeHighlightSearchMatches("")(tree);
    rehypeHighlightSearchMatches("   ")(tree);
    expect(tree).toEqual(root(paragraph(text("hello world"))));
  });

  it("wraps a single match in a <mark> with a stable match index", () => {
    const tree = root(paragraph(text("hello world")));
    rehypeHighlightSearchMatches("world")(tree);

    const p = (tree.children[0] as ElementNode);
    expect(p.children).toEqual([
      text("hello "),
      { type: "element", tagName: "mark", properties: { className: ["document-view-search-match"], dataSearchMatchIndex: 0 }, children: [text("world")] },
    ]);
  });

  it("matches case-insensitively", () => {
    const tree = root(paragraph(text("Hello WORLD")));
    rehypeHighlightSearchMatches("world")(tree);

    expect(marks(tree.children[0] as ElementNode).map((m) => (m.children[0] as TextNode).value)).toEqual([
      "WORLD",
    ]);
  });

  it("treats regex metacharacters in the query as literal text", () => {
    const tree = root(paragraph(text("a.b and aXb")));
    rehypeHighlightSearchMatches("a.b")(tree);

    const found = marks(tree.children[0] as ElementNode).map((m) => (m.children[0] as TextNode).value);
    expect(found).toEqual(["a.b"]);
  });

  it("wraps every match within a single text node without double-wrapping or skipping any", () => {
    // Regression test: a naive unist-util-visit splice that doesn't return
    // the post-splice index would re-enter the newly inserted nodes,
    // including each <mark>'s own text child, and wrap it in a second,
    // nested <mark> - or otherwise mis-walk the sibling list.
    const tree = root(paragraph(text("cat sat on the cat mat, a cat!")));
    rehypeHighlightSearchMatches("cat")(tree);

    // "cat sat on the cat mat, a cat!" starts with a match (no leading text
    // segment) and ends with a leftover "!" (a trailing text segment):
    // mark, text, mark, text, mark, text.
    const p = tree.children[0] as ElementNode;
    expect(p.children.map((c) => (c as TextNode | ElementNode).type)).toEqual([
      "element",
      "text",
      "element",
      "text",
      "element",
      "text",
    ]);

    const foundMarks = marks(p);
    expect(foundMarks).toHaveLength(3);
    for (const mark of foundMarks) {
      expect(mark.children).toHaveLength(1);
      expect((mark.children[0] as TextNode).type).toBe("text");
      expect((mark.children[0] as TextNode).value).toBe("cat");
    }
    expect(foundMarks.map((m) => m.properties.dataSearchMatchIndex)).toEqual([0, 1, 2]);

    // The plain-text segments between/around matches are untouched.
    const plainText = p.children.filter((c) => (c as TextNode | ElementNode).type === "text") as TextNode[];
    expect(plainText.map((t) => t.value)).toEqual([" sat on the ", " mat, a ", "!"]);
  });

  it("assigns sequential match indices across separate sibling text nodes in document order", () => {
    const tree = root(paragraph(text("first cat")), paragraph(text("second cat")));
    rehypeHighlightSearchMatches("cat")(tree);

    const allMarks = [
      ...marks(tree.children[0] as ElementNode),
      ...marks(tree.children[1] as ElementNode),
    ];
    expect(allMarks.map((m) => m.properties.dataSearchMatchIndex)).toEqual([0, 1]);
  });

  it("caps a large replacement without throwing or dropping any source text", () => {
    const source = "a ".repeat(300_000) + "tail";
    const p = paragraph(text(source));
    const tree = root(p);
    rehypeHighlightSearchMatches("a")(tree);

    const found = marks(p);
    expect(found).toHaveLength(MAX_DOCUMENT_SEARCH_MATCHES);
    expect(found.map((mark) => mark.properties.dataSearchMatchIndex)).toEqual(
      Array.from({ length: MAX_DOCUMENT_SEARCH_MATCHES }, (_, index) => index),
    );
    expect(textContent(p)).toBe(source);
  });

  it("shares the cap across nested elements and preserves all remaining siblings", () => {
    const inline: ElementNode = {
      type: "element", tagName: "strong", properties: {}, children: [text("CAT cat")],
    };
    const p = paragraph(text("cat ".repeat(MAX_DOCUMENT_SEARCH_MATCHES - 1)), inline, text("cat"));
    const tail = paragraph(text("another cat"));
    const originalText = textContent(p);
    rehypeHighlightSearchMatches("cat")(root(p, tail));

    expect(marks(p)).toHaveLength(MAX_DOCUMENT_SEARCH_MATCHES - 1);
    expect(marks(inline)).toHaveLength(1);
    expect(marks(inline)[0].properties.dataSearchMatchIndex).toBe(MAX_DOCUMENT_SEARCH_MATCHES - 1);
    expect(textContent(p)).toBe(originalText);
    expect(tail.children).toEqual([text("another cat")]);
  });

  it("keeps depth-first indices across inline formatting and resumes on a fresh search", () => {
    const inline: ElementNode = {
      type: "element", tagName: "em", properties: {}, children: [text("CAT")],
    };
    const p = paragraph(text("cat "), inline, text(" cat"));
    rehypeHighlightSearchMatches("cat")(root(p));
    expect(marks(p).map((mark) => mark.properties.dataSearchMatchIndex)).toEqual([0, 2]);
    expect(marks(inline)[0].properties.dataSearchMatchIndex).toBe(1);
    const fresh = paragraph(text("cat"));
    rehypeHighlightSearchMatches("cat")(root(fresh));
    expect(marks(fresh)[0].properties.dataSearchMatchIndex).toBe(0);
  });

  it("leaves a text node with no match completely untouched", () => {
    const original = paragraph(text("nothing to see here"));
    const tree = root(original);
    rehypeHighlightSearchMatches("xyz")(tree);

    expect(tree.children[0]).toBe(original);
    expect((original.children[0] as TextNode).type).toBe("text");
  });

  it("matches a single typed space against a run of multiple/irregular spaces in the source", () => {
    // Caught by adversarial review: markdown source preserves internal
    // whitespace verbatim, but the browser visually collapses any run to
    // one space - a user typing what they see on screen ("hello world",
    // one space) must still match source text like "hello   world".
    const tree = root(paragraph(text("say hello   world now")));
    rehypeHighlightSearchMatches("hello world")(tree);

    const found = marks(tree.children[0] as ElementNode).map((m) => (m.children[0] as TextNode).value);
    expect(found).toEqual(["hello   world"]);
  });

  it("matches across a newline the same way, since \\s+ covers any whitespace run", () => {
    const tree = root(paragraph(text("say hello\nworld now")));
    rehypeHighlightSearchMatches("hello world")(tree);

    const found = marks(tree.children[0] as ElementNode).map((m) => (m.children[0] as TextNode).value);
    expect(found).toEqual(["hello\nworld"]);
  });

  it("does not highlight text inside an aria-hidden element", () => {
    // Caught by adversarial review: rehype-autolink-headings appends a real
    // aria-hidden, tabIndex=-1 "#" anchor after every heading's own text -
    // without this guard, searching for "#" would highlight that decoration
    // and inflate the match count with hits unrelated to real content.
    const hiddenAnchor: ElementNode = {
      type: "element",
      tagName: "a",
      properties: { ariaHidden: true },
      children: [text("#")],
    };
    const tree = root(paragraph(text("heading"), hiddenAnchor));
    rehypeHighlightSearchMatches("#")(tree);

    expect(marks(tree.children[0] as ElementNode)).toHaveLength(0);
    expect(hiddenAnchor.children).toEqual([text("#")]);
  });

  it("still highlights a literal '#' in ordinary, non-hidden text", () => {
    const tree = root(paragraph(text("see the # symbol")));
    rehypeHighlightSearchMatches("#")(tree);

    expect(marks(tree.children[0] as ElementNode)).toHaveLength(1);
  });
});
