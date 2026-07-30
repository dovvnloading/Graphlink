import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import { visit } from "unist-util-visit";
import { toString as mdastToString } from "mdast-util-to-string";
import GithubSlugger from "github-slugger";

/**
 * Document View full redesign, stage 2 ("table of contents + reading
 * progress"). Extracts a flat heading outline directly from the RAW
 * markdown source, via its own separate remark parse pass - not by
 * querying the rendered DOM after DocumentViewMarkdown.tsx paints. This
 * means the table of contents is available (and its ids are guaranteed to
 * match what will actually render) the instant new content arrives, with
 * no render-then-query round trip and no dependency on DOM measurement.
 *
 * The id each heading gets here MUST exactly match the id
 * rehype-slug (stage 1) assigns the corresponding rendered heading, or
 * clicking a table-of-contents entry would scroll to nothing. This is
 * guaranteed by using the exact same mechanism rehype-slug itself uses
 * internally (confirmed by reading its own source, not assumed): a fresh
 * `GithubSlugger` instance per call (matching rehype-slug's own
 * `slugs.reset()` once per document), fed each heading's flattened text in
 * top-to-bottom document order (matching rehype-slug's own `visit(tree,
 * 'element', ...)` traversal) - walking the raw mdast tree
 * (`mdast-util-to-string`) rather than the rendered hast tree (rehype-slug's
 * own `hast-util-to-string`).
 *
 * Those two flattening utilities are NOT drop-in equivalents, and getting
 * this wrong was a real bug caught by adversarial review: `mdast-util-to-
 * string` special-cases `image` nodes and returns their `alt` text by
 * default, while `hast-util-to-string` treats a hast `img` as a childless
 * leaf and contributes nothing for it (confirmed by reading both packages'
 * source). A heading like `## ![Alt Text](img.png)` would otherwise extract
 * as `"Alt Text"` while actually rendering with an empty id - a dead ToC
 * link. `includeImageAlt: false` below forces the same "contributes
 * nothing" behavior on this side to match.
 *
 * remark-gfm IS included here (unlike the comment in an earlier draft of
 * this file assumed) - not because GFM syntax affects heading *detection*
 * (it doesn't), but because it affects the extracted *text*: without it, a
 * heading like `## Hello ~~World~~` would extract its literal tildes into
 * the ToC label while actually rendering with strikethrough styling and no
 * tildes. Parity with what DocumentViewMarkdown.tsx actually renders was
 * the whole point of extracting from source rather than guessing, so the
 * same remark-gfm pass belongs here too.
 */
export interface DocumentHeading {
  depth: number;
  text: string;
  id: string;
}

const headingParser = unified().use(remarkParse).use(remarkGfm);

interface HeadingNode {
  type: "heading";
  depth: number;
  children: unknown[];
}

export function extractHeadings(markdown: string): DocumentHeading[] {
  const tree = headingParser.parse(markdown);
  const slugger = new GithubSlugger();
  const headings: DocumentHeading[] = [];

  visit(tree, "heading", (node) => {
    const heading = node as unknown as HeadingNode;
    const text = mdastToString(node, { includeImageAlt: false }).trim();
    if (!text) return;
    headings.push({ depth: heading.depth, text, id: slugger.slug(text) });
  });

  return headings;
}
