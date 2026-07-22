import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useState } from "react";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";

/**
 * The HTML view node (Qt-removal plan R3.17/R3.18) - graphlink_node_htmlview.py's
 * React successor: a card that holds arbitrary, untrusted HTML (user-authored
 * today, plugin/AI-authored eventually) and previews it inside a sandboxed
 * iframe. Unlike ChatNode/CodeNode/DocumentNode/ThinkingNode, this node's
 * source string is content the LEGACY app itself renders unsafely (a bare
 * QWebEngineView with full JS + same-origin + navigation), so this rewrite
 * takes the opportunity to close that hole rather than port it faithfully:
 * the wire shape is unchanged (the html source travels on the existing
 * `content` field, exactly like ChatNode/ThinkingNode's content column - see
 * addHtmlNode below), but the render posture is deliberately much stricter
 * than the legacy widget ever was. See buildSandboxedHtmlDocument's own
 * comment for the specific security reasoning.
 *
 * Real: render (manual, source-then-Render-button - never live-on-keystroke,
 * see the state split below), collapse/expand (a real per-node toggle, same
 * ChatNode/DocumentNode manual-OR-LOD pattern - unlike CodeNode/ThinkingNode,
 * which have no manual collapse at all), delete. Deliberately, permanently
 * NOT wired (not a temporary stub - see the Popout button below): Popout /
 * standalone window view. Also deliberately not present even as a disabled
 * placeholder: "Open Document View" - there is no clear SPA equivalent for it
 * yet (matches this increment's own scope decision, not carried over from
 * any sibling node's menu).
 *
 * Card controls, not a context menu: every sibling node view above uses a
 * right-click dropdown for its actions, but this node's action surface is
 * small enough (Collapse/Expand, Popout placeholder, Delete) that it renders
 * as a plain inline button row in the header instead - no menu component,
 * no onContextMenu handler, nothing to dismiss.
 */

export interface HtmlNodeData extends Record<string, unknown> {
  htmlContent: string;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  onDelete: () => void;
}

export type HtmlFlowNode = Node<HtmlNodeData, "html">;

/**
 * buildSandboxedHtmlDocument is the ONLY place in the codebase allowed to
 * construct the iframe's srcdoc string, and it must NEVER parse, sniff, or
 * branch on what `raw` contains - no checking for an existing <head>,
 * <html>, or <body> tag in `raw`, no detecting or stripping a competing
 * <meta http-equiv="Content-Security-Policy"> tag an attacker might have
 * embedded, nothing conditional on `raw`'s bytes at all. `raw` always lands
 * verbatim in the body position of this fixed wrapper, even when `raw` is
 * itself a complete HTML document with its own head/script tags.
 *
 * This is deliberate, not an oversight: the moment this function tried to be
 * "smart" about `raw` - e.g. merging into an existing <head> it found, or
 * removing a CSP meta tag it decided was the attacker's rather than ours -
 * it would become a second HTML parser that disagrees with the browser's
 * real one about where tag boundaries fall on attacker-controlled bytes.
 * That disagreement IS the injection primitive (the same shape as every
 * mXSS / sanitizer-bypass bug: two parsers, one input, different
 * conclusions). A dumb, unconditional wrapper has no such gap - the srcdoc
 * value the browser receives is always exactly this fixed prefix, then
 * `raw` byte-for-byte unchanged, then this fixed suffix. That guarantees our
 * CSP <meta> tag is unconditionally the first thing the browser's HTML
 * parser sees, before a single byte of untrusted content, which is what
 * makes the CSP actually enforceable rather than something the untrusted
 * content could race, override, or bypass by supplying its own competing
 * <head>/<meta> first.
 */
export function buildSandboxedHtmlDocument(raw: string): string {
  return `<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; frame-src 'none'; object-src 'none'; form-action 'none'"></head><body>
${raw}
</body></html>`;
}

export function HtmlNodeView({ data, selected }: NodeProps<HtmlFlowNode>) {
  const zoom = useStore((s) => s.transform[2]);
  const lodCollapsed = zoom < LOD_ZOOM_THRESHOLD;
  const collapsed = data.isCollapsed || lodCollapsed;

  // Two separate pieces of local state, per the R3.18 spec: `sourceText`
  // tracks every keystroke in the textarea (fine - it never touches the
  // iframe), while `renderedDoc` is only ever reassigned by the Render
  // button's onClick below, via buildSandboxedHtmlDocument. The iframe's
  // srcDoc is bound to `renderedDoc` alone, never to `sourceText` - typing
  // literally cannot reach the iframe through any path in this component.
  const [sourceText, setSourceText] = useState(data.htmlContent);
  const [renderedDoc, setRenderedDoc] = useState(() => buildSandboxedHtmlDocument(data.htmlContent));

  return (
    <div className={`scene-node html-node${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}>
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-title html-node-header">
        <span>HTML</span>
        <div className="html-node-controls">
          <button type="button" className="html-node-header-btn" onClick={data.onToggleCollapse}>
            {data.isCollapsed ? "Expand" : "Collapse"}
          </button>
          <button
            type="button"
            className="html-node-header-btn"
            disabled
            title="Popout view isn't built yet - see the R3 plan doc for why a naive window.open() would be unsafe for untrusted HTML"
          >
            Popout
          </button>
          <button
            type="button"
            className="html-node-header-btn html-node-delete-btn"
            onClick={data.onDelete}
          >
            Delete
          </button>
        </div>
      </div>
      {!collapsed && (
        <div className="scene-node-body html-node-content">
          <div className="html-node-section">
            <p className="html-node-section-label">Source</p>
            <textarea
              className="html-node-source"
              value={sourceText}
              onChange={(event) => setSourceText(event.target.value)}
              spellCheck={false}
            />
          </div>
          <button
            type="button"
            className="html-node-render-btn"
            onClick={() => setRenderedDoc(buildSandboxedHtmlDocument(sourceText))}
          >
            Render
          </button>
          <div className="html-node-section">
            <p className="html-node-section-label">Preview</p>
            {/* sandbox is EXACTLY "allow-scripts" - no allow-same-origin (no
                access to this app's origin/storage/parent DOM), no
                allow-popups, no allow-top-navigation, no allow-forms, no
                allow-modals. srcDoc (never a blob: URL, never `src`, never
                dangerouslySetInnerHTML) is the only content-delivery path,
                and it only ever holds buildSandboxedHtmlDocument's output. */}
            <iframe
              className="html-node-preview"
              sandbox="allow-scripts"
              srcDoc={renderedDoc}
              title="HTML preview"
            />
          </div>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
    </div>
  );
}
