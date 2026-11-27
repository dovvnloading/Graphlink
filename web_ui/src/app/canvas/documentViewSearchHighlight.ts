/**
 * Document View full redesign, stage 3 ("in-document search/find"). The
 * first hand-authored rehype plugin in this codebase (stage 1/2 only ever
 * composed published plugins) - wraps up to the first 1,000 case-insensitive
 * substring matches in `<mark data-search-match-index>` elements,
 * directly in the hast tree, so highlighting is fully declarative rather
 * than mutating
 * the rendered DOM out from under React.
 *
 * Runs LAST in DocumentViewMarkdown.tsx's rehypePlugins array, after
 * rehype-highlight. Matching runs over the contiguous visible text of each
 * block (paragraph, list item, heading, table cell, code block...), not one
 * text node at a time, so a phrase that straddles inline formatting
 * ("**bold** and plain") or adjacent syntax-highlight tokens is ONE logical
 * match (#1167). Each fragment of that match is wrapped in its own `<mark>`
 * carrying the same `data-search-match-index`, so the markup keeps its
 * structure and Document View counts and navigates logical matches. Text
 * never joins across blocks, and anything inside an aria-hidden element
 * (heading permalinks) is excluded.
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

export const MAX_DOCUMENT_SEARCH_MATCHES = 1000;

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

// Minimal structural stand-in for unified's Node type. Only the child
// arrays, text values and immediate parent's properties are needed.
interface Tree {
  type: string;
  children?: unknown[];
}

/** Elements whose text forms one searchable run. A nested block starts its
 * own run, so text never joins across paragraphs, items or cells. */
const BLOCK_TAGS = new Set([
  "p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "pre", "blockquote",
  "dt", "dd", "figcaption", "caption", "summary", "div", "section", "article",
  "ul", "ol", "table", "thead", "tbody", "tr", "dl", "details", "hr",
]);

function isElement(node: unknown): node is ElementNode {
  return (node as Tree).type === "element";
}

export function rehypeHighlightSearchMatches(query: string) {
  const trimmed = query.trim();

  return function transform(tree: Tree) {
    if (!trimmed) return;
    const pattern = buildSearchPattern(trimmed);
    let matchIndex = 0;
    // Each matched text node's replacement fragments, applied once at the end.
    const replacements = new Map<TextNode, unknown[]>();

    /** Text leaves of one block in document order, skipping nested blocks
     * (searched on their own) and aria-hidden subtrees. */
    function collectLeaves(node: Tree, leaves: TextNode[], nested: Tree[]) {
      for (const child of node.children ?? []) {
        if ((child as Tree).type === "text") {
          leaves.push(child as TextNode);
        } else if (isElement(child)) {
          if (child.properties?.ariaHidden) continue;
          if (BLOCK_TAGS.has(child.tagName)) nested.push(child);
          else collectLeaves(child, leaves, nested);
        } else if ((child as Tree).children) {
          collectLeaves(child as Tree, leaves, nested);
        }
      }
    }

    function searchBlock(block: Tree) {
      if (matchIndex >= MAX_DOCUMENT_SEARCH_MATCHES) return;
      const leaves: TextNode[] = [];
      const nested: Tree[] = [];
      collectLeaves(block, leaves, nested);
      if (leaves.length) {
        const starts: number[] = [];
        let text = "";
        for (const leaf of leaves) {
          starts.push(text.length);
          text += leaf.value;
        }
        // Per-leaf list of [start, end, matchIndex] ranges in leaf offsets.
        const ranges = new Map<number, [number, number, number][]>();
        pattern.lastIndex = 0;
        let match: RegExpExecArray | null;
        let leafCursor = 0;
        while (matchIndex < MAX_DOCUMENT_SEARCH_MATCHES && (match = pattern.exec(text)) !== null) {
          if (match[0].length === 0) { pattern.lastIndex += 1; continue; }
          const matchStart = match.index;
          const matchEnd = matchStart + match[0].length;
          while (leafCursor < leaves.length - 1 && starts[leafCursor + 1] <= matchStart) leafCursor += 1;
          for (let leaf = leafCursor; leaf < leaves.length && starts[leaf] < matchEnd; leaf++) {
            const leafStart = starts[leaf];
            const from = Math.max(matchStart, leafStart) - leafStart;
            const to = Math.min(matchEnd, leafStart + leaves[leaf].value.length) - leafStart;
            if (to > from) {
              const list = ranges.get(leaf) ?? [];
              list.push([from, to, matchIndex]);
              ranges.set(leaf, list);
            }
          }
          matchIndex += 1;
        }
        for (const [leaf, list] of ranges) {
          const value = leaves[leaf].value;
          const fragments: unknown[] = [];
          let last = 0;
          for (const [from, to, index] of list) {
            if (from > last) fragments.push({ type: "text", value: value.slice(last, from) });
            fragments.push({
              type: "element",
              tagName: "mark",
              properties: { className: ["document-view-search-match"], dataSearchMatchIndex: index },
              children: [{ type: "text", value: value.slice(from, to) }],
            });
            last = to;
          }
          if (last < value.length) fragments.push({ type: "text", value: value.slice(last) });
          replacements.set(leaves[leaf], fragments);
        }
      }
      for (const child of nested) searchBlock(child);
    }

    /** Replace matched text nodes in place, rebuilding each changed array once. */
    function applyReplacements(node: Tree) {
      const children = node.children;
      if (!children) return;
      let rewritten: unknown[] | undefined;
      for (let index = 0; index < children.length; index++) {
        const child = children[index];
        const fragments = replacements.get(child as TextNode);
        if (fragments) {
          rewritten ??= children.slice(0, index);
          rewritten.push(...fragments);
          continue;
        }
        if ((child as Tree).children) applyReplacements(child as Tree);
        rewritten?.push(child);
      }
      if (rewritten) node.children = rewritten;
    }

    searchBlock(tree);
    if (replacements.size) applyReplacements(tree);
  };
}
