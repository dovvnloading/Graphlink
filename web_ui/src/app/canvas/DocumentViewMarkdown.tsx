import { useRef, useState, type JSX } from "react";
import ReactMarkdown, { type ExtraProps } from "react-markdown";
import remarkGfm from "remark-gfm";
import { remarkAlert } from "remark-github-blockquote-alert";
import rehypeHighlight from "rehype-highlight";
import rehypeSlug from "rehype-slug";
import rehypeAutolinkHeadings from "rehype-autolink-headings";
import rehypeExternalLinks from "rehype-external-links";
import Zoom from "react-medium-image-zoom";
import "react-medium-image-zoom/dist/styles.css";
import { rehypeHighlightSearchMatches } from "./documentViewSearchHighlight";

/**
 * Document View's enhanced markdown renderer (full redesign, stage 1 of 4 -
 * "content rendering upgrades"). Extracted out of DocumentViewPanel.tsx into
 * its own file/component so the render pipeline (plugins + component
 * overrides) is independently readable and testable, rather than an inline
 * `<ReactMarkdown>` call buried in the panel's own JSX.
 *
 * Deliberately scoped to Document View only - NOT a shared "MarkdownRenderer"
 * swapped into ChatNodeView/NoteNodeView/etc, which each keep their own,
 * simpler `<ReactMarkdown remarkPlugins={[remarkGfm]}
 * rehypePlugins={[rehypeHighlight]}>` untouched. The user's own request was
 * about "the document viewer" specifically; broadening this to every
 * markdown surface in the app would be a much larger, different-shaped
 * change than what was asked for.
 *
 * Plugin pipeline, in order (order matters for the first two - autolink
 * needs the ids rehype-slug just assigned):
 * 1. rehype-slug: gives every heading a stable `id` (needed by Stage 2's
 *    table of contents to scroll/highlight by anchor, and usable right now
 *    as a plain URL fragment).
 * 2. rehype-autolink-headings: appends a hover-revealed "#" link after each
 *    heading's own text (the GitHub/Docusaurus/Mintlify convention) -
 *    `ariaHidden`/`tabIndex: -1` kept explicit (this plugin's own default for
 *    "append", but overriding `properties` at all replaces that default, so
 *    it must be restated here) so it never distracts screen-reader/keyboard
 *    navigation from the real heading text.
 * 3. rehype-highlight: unchanged from the existing chat/note markdown setup
 *    - still the source of the `hljs`/`language-xxx` classes the CodeBlock
 *    override below reads to show a language badge.
 * 4. rehype-external-links: adds `rel`/`target` only to absolute http(s)
 *    links (verified directly against its own source - it explicitly skips
 *    relative paths and `#fragment` links, so the anchor links step 2 just
 *    added are never touched by this step). `target: "_blank"` is
 *    deliberate here specifically (most rehype-external-links guidance
 *    recommends AGAINST setting a target) because this app runs inside a
 *    native pywebview shell, not a normal browser tab - letting an external
 *    link navigate the app's own window away from GraphLink would be worse
 *    than opening a new one.
 *
 * remark-github-blockquote-alert adds GitHub's `> [!NOTE]`/`[!TIP]`/
 * `[!IMPORTANT]`/`[!WARNING]`/`[!CAUTION]` blockquote-alert syntax, styled
 * via this app's own gl-semantic-status and gl-surface CSS custom
 * properties in styles.css rather than importing the package's own
 * alert.css (which hard-codes GitHub's own light/dark color variables, not
 * this app's theme system).
 *
 * 5. rehypeHighlightSearchMatches (stage 3, "in-document search/find"): runs
 *    last, after rehype-highlight has already tokenized code blocks into
 *    their own syntax-highlighting spans - see that plugin's own doc
 *    comment (documentViewSearchHighlight.ts) for why it only matches
 *    within a single text node, and why the query is regex-escaped first.
 *    A no-op when `searchQuery` is empty/undefined, so the common case
 *    (search bar closed) adds no extra tree-walk cost beyond an early return.
 */

function CodeBlock({ node: _node, children, ...props }: JSX.IntrinsicElements["pre"] & ExtraProps) {
  const preRef = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);

  // Fenced code blocks are always `pre > code` (one child) - the language
  // lives on that inner <code> element's own className (rehype-highlight's
  // "hljs language-xxx" shape), not on this <pre>'s.
  const codeChild = Array.isArray(children) ? children[0] : children;
  const codeClassName =
    codeChild !== null && typeof codeChild === "object" && "props" in codeChild
      ? ((codeChild.props as { className?: string }).className ?? "")
      : "";
  const languageMatch = /language-(\w+)/.exec(codeClassName);
  const language = languageMatch ? languageMatch[1] : "";

  function onCopy() {
    // Reading .textContent off the rendered DOM node (rather than trying to
    // reconstruct the source string by walking React children) sidesteps
    // rehype-highlight's own deeply-nested syntax-highlighting <span>s -
    // .textContent flattens through any depth of those for free.
    const text = preRef.current?.textContent ?? "";
    if (!text) return;
    navigator.clipboard
      ?.writeText(text)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      // See NodeMarkdown.tsx's identical handler for why this .catch exists
      // (ADR-011 stage 11.6's clipboard sweep missed four sites; this is one).
      .catch((error: unknown) => {
        console.error("Failed to copy code block to clipboard", error);
      });
  }

  return (
    <div className="document-view-code-block">
      <div className="document-view-code-block-header">
        <span className="document-view-code-block-language">{language || "text"}</span>
        <button
          type="button"
          className="document-view-code-block-copy"
          onClick={onCopy}
          title="Copy code"
          aria-label="Copy code"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre {...props} ref={preRef}>
        {children}
      </pre>
    </div>
  );
}

function TableWrapper({ node: _node, ...props }: JSX.IntrinsicElements["table"] & ExtraProps) {
  // remark-gfm parses pipe tables but neither remark nor rehype ships a
  // plugin that also contains the resulting <table> in a scrollable
  // wrapper - a well-documented gap in the ecosystem, not an oversight here.
  return (
    <div className="document-view-table-wrapper">
      <table {...props} />
    </div>
  );
}

function ZoomImage({ node: _node, alt, src, ...props }: JSX.IntrinsicElements["img"] & ExtraProps) {
  // wrapElement="span": an <img> can legally appear INLINE inside a
  // <p> (e.g. "see this diagram: ![...](...)"), and Zoom's own default
  // wrapper is a <div> - a block element nested inside a <p> is invalid
  // HTML and would trigger React's own DOM-nesting warning.
  //
  // `alt` is destructured out (rather than left folded into `...props`) so
  // eslint-plugin-jsx-a11y can see a real, explicit alt attribute here: the
  // markdown source's `![alt text](src)` syntax is what populates it via
  // ReactMarkdown's own img props, and `?? ""` covers the `![](src)` case
  // (no alt text authored) by falling back to an explicitly-decorative
  // empty alt rather than leaving it undefined.
  //
  // SECURITY-FIX (markdown-image-exfil): a hostile saved chat/imported
  // document is this file's own attack vector for the same gap
  // NodeMarkdown.tsx's own ZoomImage closes (see that file's copy of this
  // comment for the full mechanism) - this is a genuinely separate
  // component, not a shared import, so the fix is duplicated here rather
  // than left half-applied. Same explicit http(s)-only allowlist this
  // file's rehype-external-links pass already applies to <a> hrefs
  // (`target`/`rel`, configured below in DocumentViewMarkdown itself),
  // mirrored here for `src`: a non-http(s) `src` renders nothing. Actual
  // arbitrary-host exfiltration over a syntactically-valid http(s) `src`
  // is closed at the network layer instead, by backend/app.py's img-src
  // CSP.
  const isHttpUrl = !!src && /^https?:\/\//i.test(src.trim());
  if (!isHttpUrl) {
    return null;
  }
  return (
    <Zoom wrapElement="span">
      {/* referrerPolicy="no-referrer": defense-in-depth alongside the CSP
          above - never hand the image host this app's own URL as a
          Referer. `{...props}` spread first, `src`/referrerPolicy last,
          so nothing upstream can silently override them. */}
      <img alt={alt ?? ""} {...props} src={src} referrerPolicy="no-referrer" />
    </Zoom>
  );
}

export function DocumentViewMarkdown({ content, searchQuery }: { content: string; searchQuery?: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkAlert]}
      rehypePlugins={[
        rehypeSlug,
        [
          rehypeAutolinkHeadings,
          {
            behavior: "append",
            properties: { className: ["document-view-heading-anchor"], ariaHidden: true, tabIndex: -1 },
            content: { type: "text", value: "#" },
          },
        ],
        rehypeHighlight,
        [rehypeExternalLinks, { target: "_blank", rel: ["nofollow", "noopener", "noreferrer"] }],
        [rehypeHighlightSearchMatches, searchQuery ?? ""],
      ]}
      components={{ pre: CodeBlock, table: TableWrapper, img: ZoomImage }}
    >
      {content}
    </ReactMarkdown>
  );
}
