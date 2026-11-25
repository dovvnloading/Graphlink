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

export function rehypeHighlightSearchMatches(query: string) {
  const trimmed = query.trim();

  return function transform(tree: Tree) {
    if (!trimmed) return;
    const pattern = buildSearchPattern(trimmed);
    let matchIndex = 0;

    // Rebuild each changed sibling array once, in document order, instead
    // of passing an unbounded replacement array as splice arguments. Never
    // visit newly-created marks. Stop looking for matches at the shared
    // limit, keeping the unhighlighted remainder of the document intact.
    function highlightChildren(parent: Tree) {
      const children = parent.children;
      if (!children) return;
      let rewritten: unknown[] | undefined;
      for (let index = 0; index < children.length; index++) {
        if (matchIndex >= MAX_DOCUMENT_SEARCH_MATCHES) {
          if (rewritten) parent.children = rewritten.concat(children.slice(index));
          return;
        }
        const child = children[index] as Tree;
        if (child.type !== "text") {
          highlightChildren(child);
          rewritten?.push(child);
          continue;
        }
        // The heading permalink is decorative, not searchable. As before,
        // check the immediate parent (where rehype-autolink sets ariaHidden).
        if ((parent as ElementNode).properties?.ariaHidden) {
          rewritten?.push(child);
          continue;
        }
        const node = child as TextNode;
        pattern.lastIndex = 0;
        let match = pattern.exec(node.value);
        if (!match) {
          rewritten?.push(child);
          continue;
        }
        rewritten ??= children.slice(0, index);
        let lastEnd = 0;
        do {
          const start = match.index;
          const end = start + match[0].length;
          if (start > lastEnd) {
            rewritten.push({ type: "text", value: node.value.slice(lastEnd, start) });
          }
          rewritten.push({
            type: "element",
            tagName: "mark",
            properties: { className: ["document-view-search-match"], dataSearchMatchIndex: matchIndex },
            children: [{ type: "text", value: match[0] }],
          });
          matchIndex += 1;
          lastEnd = end;
        } while (matchIndex < MAX_DOCUMENT_SEARCH_MATCHES && (match = pattern.exec(node.value)) !== null);
        if (lastEnd < node.value.length) {
          rewritten.push({ type: "text", value: node.value.slice(lastEnd) });
        }
      }
      if (rewritten) parent.children = rewritten;
    }

    highlightChildren(tree);
  };
}
