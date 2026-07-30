import { visit } from "unist-util-visit";

/**
 * Document View full redesign, stage 3 ("in-document search/find"). The
 * first hand-authored rehype plugin in this codebase (stage 1/2 only ever
 * composed published plugins) - wraps every case-insensitive substring
 * match of a search query in a `<mark data-search-match-index>` element,
 * directly in the hast tree, so highlighting is fully declarative (re-render
 * on every keystroke, like the rest of this pipeline) rather than mutating
 * the rendered DOM out from under React.
 *
 * Runs LAST in DocumentViewMarkdown.tsx's rehypePlugins array, after
 * rehype-highlight - code blocks are already tokenized into many small
 * per-token `<span>` elements by that point. This plugin only ever matches
 * within a SINGLE text node, so a query that spans two adjacent syntax-
 * highlighted tokens (or two adjacent bits of inline formatting, e.g. a
 * query straddling **bold** and plain text) will not be found. This is a
 * deliberate, documented scope boundary, not an oversight: highlighting a
 * match that spans multiple hast elements would require rewriting sibling
 * structure, not just splitting one text node, and is out of scope for a
 * document viewer's find feature.
 *
 * The query is escaped before use (`escapeSearchRegExp`) so a user typing
 * regex metacharacters (`.`, `*`, `(`, etc.) searches for that literal text,
 * not a regex - both for correctness (users expect literal substring
 * search, not regex search) and safety (an unescaped user-controlled
 * pattern fed to `RegExp` is a classic ReDoS vector).
 *
 * A literal run of one-or-more spaces in the query is widened to `\s+`
 * (buildSearchPattern) after escaping - caught by adversarial review:
 * markdown source text preserves internal whitespace verbatim (multiple
 * spaces, a line-wrapped newline) while the browser's default
 * `white-space: normal` visually collapses any such run to a single space,
 * so a user typing what they see on screen ("hello world", one space)
 * would otherwise silently fail to match source text like "hello   world"
 * (three spaces, e.g. from a pasted document). This does NOT apply to the
 * escaped regex metacharacters themselves - only bare, unescaped space
 * characters are widened.
 *
 * Case-insensitivity uses plain JS RegExp "i" semantics, not full
 * Unicode-aware case folding - e.g. German "straße" will not match a query
 * of "STRASSE", nor will Turkish dotted/dotless i/I variants cross-match.
 * This is a known, accepted limitation shared with native browser Ctrl+F
 * (which has the same gap), not a regression unique to this plugin - full
 * locale-aware collation is out of scope for a lightweight highlight-as-
 * you-type feature.
 */

const REGEXP_SPECIAL_CHARS_RE = /[.*+?^${}()|[\]\\]/g;

export function escapeSearchRegExp(value: string): string {
  return value.replace(REGEXP_SPECIAL_CHARS_RE, "\\$&");
}

function buildSearchPattern(trimmedQuery: string): RegExp {
  const escaped = escapeSearchRegExp(trimmedQuery).replace(/ +/g, "\\s+");
  return new RegExp(escaped, "gi");
}

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

type MatchNode = TextNode | ElementNode;

// Minimal structural stand-in for unified's own `Node` type (just
// `{type: string}`), matching this codebase's existing convention
// (documentViewHeadings.ts) of hand-rolling a small local interface rather
// than adding a dependency on the official unist/hast/mdast type packages.
interface Tree {
  type: string;
  children?: unknown[];
}

export function rehypeHighlightSearchMatches(query: string) {
  const trimmed = query.trim();

  return function transform(tree: Tree) {
    if (!trimmed) return;
    const pattern = buildSearchPattern(trimmed);
    let matchIndex = 0;

    visit(tree, "text", (node: TextNode, index, parent) => {
      if (typeof index !== "number" || !parent || !("children" in parent)) return;
      // Skip decorative content invisible to assistive tech - concretely,
      // rehype-autolink-headings' own appended "#" anchor (aria-hidden,
      // tabIndex -1) after every heading. Caught by adversarial review:
      // without this guard, searching for "#" highlighted that decoration
      // next to every heading and inflated the match count/navigation with
      // hits that have nothing to do with the document's real content.
      // Checks the IMMEDIATE parent only (not the full ancestor chain) -
      // sufficient for this specific, known case, since the anchor's
      // aria-hidden property sits directly on the "#" text node's parent.
      const parentProperties = (parent as unknown as ElementNode).properties;
      if (parentProperties && parentProperties.ariaHidden) return;
      pattern.lastIndex = 0;
      if (!pattern.test(node.value)) return;
      pattern.lastIndex = 0;

      const replacement: MatchNode[] = [];
      let lastEnd = 0;
      let match: RegExpExecArray | null;
      while ((match = pattern.exec(node.value)) !== null) {
        const start = match.index;
        const end = start + match[0].length;
        if (start > lastEnd) {
          replacement.push({ type: "text", value: node.value.slice(lastEnd, start) });
        }
        replacement.push({
          type: "element",
          tagName: "mark",
          properties: { className: ["document-view-search-match"], dataSearchMatchIndex: matchIndex },
          children: [{ type: "text", value: match[0] }],
        });
        matchIndex += 1;
        lastEnd = end;
        // A zero-length query can never reach here (escapeSearchRegExp'd
        // non-empty `trimmed` always matches at least one character), so no
        // infinite-loop guard is needed for lastIndex staying put.
      }
      if (lastEnd < node.value.length) {
        replacement.push({ type: "text", value: node.value.slice(lastEnd) });
      }

      (parent as unknown as { children: unknown[] }).children.splice(index, 1, ...replacement);

      // unist-util-visit-parents reads the parent's children array fresh on
      // every loop iteration - after splicing 1 node into `replacement.length`
      // nodes, its default "continue at index + 1" would walk straight back
      // into the very nodes just inserted here, including the freshly built
      // <mark>'s own text child (which trivially re-matches the same
      // pattern) and would get wrapped in a second, nested <mark>. Returning
      // the post-splice index explicitly (the documented mechanism for this
      // exact case - see unist-util-visit's own Visitor jsdoc) skips past
      // everything just inserted and resumes at the real next sibling.
      return index + replacement.length;
    });
  };
}
