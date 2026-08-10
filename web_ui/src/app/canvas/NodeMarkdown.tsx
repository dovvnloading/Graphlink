import { useRef, useState, type JSX } from "react";
import ReactMarkdown, { type ExtraProps } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import { remarkAlert } from "remark-github-blockquote-alert";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import Zoom from "react-medium-image-zoom";
import "react-medium-image-zoom/dist/styles.css";
import "katex/dist/katex.min.css";
import { useCanvasSearchQuery } from "./CanvasSearchContext";
import { rehypeHighlightSearchMatches } from "./documentViewSearchHighlight";

/**
 * Shared markdown renderer for scene NODE cards (node redesign, stage 1 of 4
 * - "shared node markdown renderer"). Every text-bearing node kind (Chat,
 * Note, Conversation, Artifact, Code, CodeSandbox, Gitlink, PyCoder,
 * Thinking, WebResearch - confirmed via grep, not assumed) rendered a bare
 * `<ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>`
 * with none of Document View's content-rendering upgrades (code-block copy/
 * language badge, wide-table scroll wrapper, image zoom, GitHub callouts,
 * hardened external links). This brings all of those to every node kind at
 * once, through one shared component, rather than duplicating the pipeline
 * ten times.
 *
 * Node redesign, stage 4: LaTeX math via remark-math + rehype-katex (inline
 * `$...$` and block `$$...$$`), scoped to this file only - DocumentViewMarkdown.tsx
 * does not render attached documents' math today, and adding it there is a
 * separate, un-asked-for change. katex.min.css is imported directly here
 * (same "import the library's own dist CSS in this file" pattern
 * react-medium-image-zoom's own stylesheet already uses just below) rather
 * than duplicated into styles.css.
 *
 * A SIBLING of DocumentViewMarkdown.tsx, not a shared import from it -
 * mirrors that file's own doc comment's reasoning in the opposite direction:
 * the two surfaces have genuinely different needs, not just different class
 * names. Specifically excluded here, both deliberately:
 *
 * - Heading anchors (rehype-slug + rehype-autolink-headings): meaningless
 *   without a table of contents to link to, AND a real correctness hazard -
 *   github-slugger's ids are unique only WITHIN one document. Document View
 *   only ever shows one document at a time, but dozens of node cards render
 *   simultaneously on the same canvas; two nodes both containing "## Notes"
 *   would each get `id="notes"`, a genuine DOM-id collision across
 *   currently-mounted elements that Document View's single-document
 *   architecture never has to worry about.
 *
 * ADR-012 stage 12.5 added search-match highlighting here too, reusing
 * documentViewSearchHighlight.ts's own rehypeHighlightSearchMatches plugin
 * unchanged (down to its hardcoded "document-view-search-match" class,
 * which styles.css's own `mark.document-view-search-match` rule matches by
 * class alone - no ancestor-scoping - so it paints correctly on a node card
 * exactly as it does inside Document View) - the query comes from
 * CanvasSearchContext (SearchOverlay.tsx's own input), not a prop, since
 * this file is instantiated by every node card on the canvas and prop-
 * threading the live query through 15 *NodeView.tsx components' own `data`
 * shape for one leaf renderer would be pure duplication. Unlike Document
 * View's own DocumentViewSearch.tsx, there is no per-node "current match"
 * concept here - SearchOverlay's Next/Previous jumps between NODES (it
 * recenters the viewport), it does not track a match index within any one
 * node's rendered text, so every node highlights every occurrence of the
 * live query identically, with no `-current` variant ever applied.
 *
 * Component overrides use a `node-md-` class prefix (not `document-view-`)
 * but are styled to EXTEND `.chat-node-content`'s existing shared base
 * rules (headings/p/li/code/table/hljs in styles.css) exactly the way
 * Document View's own `.document-view-code-block`/`.document-view-table-wrapper`
 * extend that same base class - both surfaces' scroll containers carry the
 * `chat-node-content` class, so both automatically pick up node redesign
 * stage 2's typography improvements to that shared base, not just this
 * stage's own new wrapper chrome.
 *
 * SafeAnchor closes a real gap found while wiring this in: WebResearchNodeView
 * already had a bespoke `a` component override stripping the href from any
 * non-http(s)-scheme link (its own comment: "answerMarkdown is LLM-generated
 * from untrusted web evidence, so a javascript:/file: scheme must never be
 * allowed to navigate") - but every OTHER node kind rendered links with zero
 * such guard, despite their own content being no less LLM-generated (and so
 * no less exposed to the same prompt-injection-driven-malicious-markdown
 * class of risk WebResearchNodeView was specifically defending against).
 * Baking the same guard into NodeMarkdown's own default `a` override, rather
 * than leaving it as one view's bespoke fix, closes that gap for all call
 * sites at once and lets WebResearchNodeView delete its now-redundant copy.
 *
 * SafeAnchor is the SOLE source of both the security decision and the
 * resulting hardening attributes - deliberately not split across two
 * mechanisms. An earlier draft of this file also ran rehype-external-links
 * (a separate, hast-level pass) to supply `target="_blank"`/safe `rel` for
 * genuine http(s) links, relying on SafeAnchor's own `{...props}` spread to
 * carry those attributes through. Adversarial review found this created a
 * real gap: SafeAnchor's own scheme check is case-INsensitive (`/i` flag,
 * matching a user typing "HTTPS://..."), but rehype-external-links' default
 * `protocols` match is a case-SENSITIVE array `.includes()` check - so a
 * mixed-case scheme passed SafeAnchor's allowlist (rendered as a real,
 * clickable link) while silently NEVER receiving target/rel from the other
 * plugin, since its own protocol comparison never matched. No dangerous
 * scheme was ever admitted either way (javascript:/file:/data: are rejected
 * case-insensitively by SafeAnchor itself, independent of the other plugin
 * entirely) - but a safe-looking http(s) link could end up open in the same
 * tab with a full Referer leak, silently weaker than intended. Removing
 * rehype-external-links entirely and having SafeAnchor set its own
 * `target`/`rel` directly (spread BEFORE them, so they can never be
 * silently overridden by anything upstream) closes this by construction:
 * there is now exactly one scheme check, and its result is what determines
 * both whether the link renders at all AND how hardened it is.
 */

function CodeBlock({ node: _node, children, ...props }: JSX.IntrinsicElements["pre"] & ExtraProps) {
  const preRef = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);

  // Fenced code blocks are always `pre > code` (one child) - the language
  // lives on that inner <code> element's own className (rehype-highlight's
  // "hljs language-xxx" shape), not on this <pre>'s. Same extraction as
  // DocumentViewMarkdown.tsx's own CodeBlock.
  const codeChild = Array.isArray(children) ? children[0] : children;
  const codeClassName =
    codeChild !== null && typeof codeChild === "object" && "props" in codeChild
      ? ((codeChild.props as { className?: string }).className ?? "")
      : "";
  const languageMatch = /language-(\w+)/.exec(codeClassName);
  const language = languageMatch ? languageMatch[1] : "";

  function onCopy() {
    const text = preRef.current?.textContent ?? "";
    if (!text) return;
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div className="node-md-code-block">
      <div className="node-md-code-block-header">
        <span className="node-md-code-block-language">{language || "text"}</span>
        <button
          type="button"
          className="node-md-code-block-copy nodrag"
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
  // Fixes a real, pre-existing bug, not just a nicety: node cards have a
  // fixed width (420px for chat nodes) and .chat-node-content only ever set
  // overflow-y, never overflow-x - a wide table had no way to be scrolled
  // into view at all, just silently clipped by the card's own edge.
  return (
    <div className="node-md-table-wrapper">
      <table {...props} />
    </div>
  );
}

function ZoomImage({ node: _node, alt, ...props }: JSX.IntrinsicElements["img"] & ExtraProps) {
  // alt comes from the markdown source itself (`![alt](url)`) via
  // react-markdown's own parsing - pass it through explicitly rather than
  // relying on the `{...props}` spread, which satisfies jsx-a11y/alt-text
  // (a spread alone isn't statically verifiable) while keeping the real
  // author-provided text. Empty alt is a legitimate markdown state
  // (`![](url)`), not a suppression - falls back to "" rather than undefined.
  return (
    <Zoom wrapElement="span">
      <img alt={alt ?? ""} {...props} />
    </Zoom>
  );
}

function SafeAnchor({ node: _node, href, children, ...props }: JSX.IntrinsicElements["a"] & ExtraProps) {
  const isHttpUrl = !!href && /^https?:\/\//i.test(href.trim());
  if (!isHttpUrl) {
    // No href at all for a non-http(s) scheme (javascript:, file:, data:,
    // etc.) - omitting it entirely removes the native middle-click/
    // "open link in new tab"/"copy link" context-menu escape hatch a mere
    // onClick+preventDefault guard would leave behind (those browser
    // affordances read the raw href attribute directly, bypassing onClick).
    return <>{children}</>;
  }
  return (
    // `{...props}` spread FIRST, then href/target/rel explicit and LAST -
    // these three are the ones this component's own security decision
    // governs, so nothing upstream can silently override them.
    <a {...props} href={href} target="_blank" rel="nofollow noopener noreferrer">
      {children}
    </a>
  );
}

export function NodeMarkdown({ content }: { content: string }) {
  const searchQuery = useCanvasSearchQuery();
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkAlert, remarkMath]}
      rehypePlugins={[rehypeHighlight, rehypeKatex, [rehypeHighlightSearchMatches, searchQuery]]}
      components={{ pre: CodeBlock, table: TableWrapper, img: ZoomImage, a: SafeAnchor }}
    >
      {content}
    </ReactMarkdown>
  );
}
