import type { Node, NodeProps } from "@xyflow/react";
import { memo, useEffect, useRef, useState } from "react";
import {
  HTML_SPLIT_DEFAULT,
  HTML_SPLIT_MAX,
  HTML_SPLIT_MIN,
  HTML_SPLIT_TOTAL_PX,
  HTML_SPLITTER_REPORT_DEBOUNCE_MS,
} from "./canvasConstants";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";

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
 * NOT wired: Popout / standalone window view - opening untrusted HTML in a
 * separate window needs a security review first. It is not rendered at all,
 * not even as a disabled button: a control that can never be used is noise
 * on a card, and the reason for its absence belongs here rather than in a
 * tooltip nobody can act on. Also deliberately absent: "Open Document View"
 * - there is no clear SPA equivalent for it yet (matches this increment's
 * own scope decision, not carried over from any sibling node's menu).
 *
 * Card controls, not a context menu: every sibling node view above uses a
 * right-click dropdown for its actions, but this node's action surface is
 * small enough (Collapse/Expand, Popout placeholder, Delete) that it renders
 * as a plain inline button row in the header instead - no menu component,
 * no onContextMenu handler, nothing to dismiss.
 *
 * R6.3: the Source/Preview split position (splitterValue, a fraction of
 * HTML_SPLIT_TOTAL_PX) is now a real draggable divider between the two
 * panes, not just fixed 140px/140px panes as before. This was a deliberate
 * scope cut back in R3.17/R3.18 (legacy's own splitter_state had "no domain
 * meaning" at the time - see styles.css's own now-superseded comment on
 * .html-node-source) - R6.3 re-scopes it in because R6.4/R6.5's session
 * load/save pipeline needs every legacy-persisted field to round-trip
 * losslessly, this one included (see canvasConstants.ts's own HTML_SPLIT_*
 * doc). Dragging updates the visible split immediately (no debounce on the
 * live drag itself - only the final settled value gets reported over the
 * wire, via makeDebouncedSplitterReport below, same "report once settled"
 * posture as ChartNodeView.tsx's resize debounce).
 */

export interface HtmlNodeData extends Record<string, unknown> {
  htmlContent: string;
  isCollapsed: boolean;
  // R6.3: null means "no saved split position yet" (a brand-new node, or one
  // loaded from before this field existed) - HTML_SPLIT_DEFAULT is applied
  // locally in that case, never sent back over the wire as a fake "0.5" the
  // backend would think was a real user choice.
  htmlSplitterState: number | null;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onSplitterChange: (value: number) => void;
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
 *
 * Base URL: an iframe[srcdoc] document with no <base> element of its own
 * falls back to its CONTAINER document's base URL (the HTML spec's
 * "fallback base url" algorithm) - here that's this app's own window,
 * http://127.0.0.1:<port>/#token=<token> (see lib/auth/token.ts for why the
 * live capability token lives in the fragment rather than ever being
 * stripped from location.hash). The sandbox attribute's missing
 * allow-same-origin (see the iframe's own comment below) stops script in
 * this frame from reaching the app's real DOM/storage/network origin, but
 * that is a SEPARATE mechanism from document.baseURI and does not touch it
 * at all - untrusted script could previously read the live token straight
 * out of `new URL(document.baseURI).hash` with no origin check involved.
 * SANDBOX_BASE_ORIGIN below closes that: per spec, the first <base>
 * element with an href in tree order (ours, unconditionally first, for the
 * same "raw never gets parsed/sniffed" reason as above) freezes the
 * document's base url to that value instead of falling back to the
 * container's. The CSP's own base-uri directive has to explicitly allow
 * this exact origin (rather than staying 'none') because base-uri
 * validates ANY <base> element's href before letting it take effect,
 * including this trusted, wrapper-authored one - leaving it 'none' would
 * make the browser reject our own <base> too and silently fall back to the
 * token-bearing base url this fix exists to avoid, turning the fix into a
 * silent no-op. SANDBOX_BASE_ORIGIN is an RFC 2606 .invalid host (never
 * resolvable, never a real page) so the placeholder can't collide with
 * anything network-reachable, though every network-capable directive here
 * (default-src/connect-src/img-src/frame-src/object-src) already forbids
 * fetching it regardless - it only has to be a syntactically valid,
 * token-free absolute URL for document.baseURI to report.
 */
const SANDBOX_BASE_ORIGIN = "http://sandboxed-html-node.invalid";

export function buildSandboxedHtmlDocument(raw: string): string {
  return `<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri ${SANDBOX_BASE_ORIGIN}; frame-src 'none'; object-src 'none'; form-action 'none'"><base href="${SANDBOX_BASE_ORIGIN}/"></head><body>
${raw}
</body></html>`;
}

/** R6.3: clamps a raw drag-derived fraction into [HTML_SPLIT_MIN,
 * HTML_SPLIT_MAX] so a fast/far drag can never collapse either pane to zero
 * (or negative) height - exported standalone for direct unit testing of the
 * clamp boundaries without simulating a full pointer-drag sequence. */
export function clampSplitterValue(value: number): number {
  return Math.min(HTML_SPLIT_MAX, Math.max(HTML_SPLIT_MIN, value));
}

/** R6.3: the debounce wrapper for splitter-position reporting - same
 * plain-clearTimeout/setTimeout-box-keyed-off-the-caller's-own-timerRef
 * shape as ChartNodeView.tsx's makeDebouncedChartResize, exported standalone
 * for the same direct-unit-testability reason. */
export function makeDebouncedSplitterReport(
  timerRef: { current: ReturnType<typeof setTimeout> | null },
  onSplitterChange: (value: number) => void,
  debounceMs: number = HTML_SPLITTER_REPORT_DEBOUNCE_MS,
): (value: number) => void {
  return (value) => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      onSplitterChange(value);
    }, debounceMs);
  };
}

function HtmlNodeViewImpl({ data, selected }: NodeProps<HtmlFlowNode>) {
  const lodCollapsed = useLodVisibility();
  const collapsed = data.isCollapsed || lodCollapsed;

  // Two separate pieces of local state, per the R3.18 spec: `sourceText`
  // tracks every keystroke in the textarea (fine - it never touches the
  // iframe), while `renderedDoc` is only ever reassigned by the Render
  // button's onClick below, via buildSandboxedHtmlDocument. The iframe's
  // srcDoc is bound to `renderedDoc` alone, never to `sourceText` - typing
  // literally cannot reach the iframe through any path in this component.
  const [sourceText, setSourceText] = useState(data.htmlContent);
  const [renderedDoc, setRenderedDoc] = useState(() => buildSandboxedHtmlDocument(data.htmlContent));

  // R6.3: the Source/Preview split - restored from data.htmlSplitterState on
  // mount (see the component-level useState initializer), then dragged
  // locally via onSplitterPointerDown below. splitterTimerRef carries the
  // debounce state across drags (see makeDebouncedSplitterReport above).
  const [splitterValue, setSplitterValue] = useState(data.htmlSplitterState ?? HTML_SPLIT_DEFAULT);
  const splitterTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (splitterTimerRef.current) clearTimeout(splitterTimerRef.current);
    },
    [],
  );

  // Plain window-level pointermove/pointerup listeners (added on pointerdown,
  // removed on pointerup) rather than setPointerCapture - jsdom implements no
  // setPointerCapture/releasePointerCapture at all (confirmed during recon),
  // so calling either would break every test simulating this drag; window
  // listeners work identically in a real browser and need no such API.
  // `latestValue` is a plain mutable box read synchronously by onPointerUp -
  // NOT the splitterValue React state var, which only updates on the next
  // render and would risk a stale read if onPointerUp ran before that render
  // flushed.
  function onSplitterPointerDown(event: React.PointerEvent<HTMLDivElement>) {
    const startY = event.clientY;
    const startValue = splitterValue;
    const latestValue = { current: startValue };

    function onPointerMove(moveEvent: PointerEvent) {
      const next = clampSplitterValue(startValue + (moveEvent.clientY - startY) / HTML_SPLIT_TOTAL_PX);
      latestValue.current = next;
      setSplitterValue(next);
    }
    function onPointerUp() {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      // A plain click with no real movement is a no-op - don't fire a
      // network round trip for an unchanged value.
      if (latestValue.current !== startValue) {
        makeDebouncedSplitterReport(splitterTimerRef, data.onSplitterChange)(latestValue.current);
      }
    }
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
  }

  const sourceHeightPx = Math.round(splitterValue * HTML_SPLIT_TOTAL_PX);
  const previewHeightPx = HTML_SPLIT_TOTAL_PX - sourceHeightPx;

  return (
    <NodeShell
      kindClassName="html-node"
      selected={!!selected}
      collapsed={collapsed}
      header={
        <div className="scene-node-title html-node-header">
          <span>HTML</span>
          <div className="html-node-controls">
            <button type="button" className="html-node-header-btn" onClick={data.onToggleCollapse}>
              {data.isCollapsed ? "Expand" : "Collapse"}
            </button>
            {/* No Popout control. It shipped as a permanently disabled
                button with its reason in a tooltip, which is a dead control
                on a card the user cannot do anything about - the rationale
                is recorded in this module's own doc instead: opening
                untrusted HTML in a separate window needs a security review
                first, and until that happens there is nothing to render. */}
            <button
              type="button"
              className="html-node-header-btn html-node-delete-btn"
              onClick={data.onDelete}
            >
              Delete
            </button>
          </div>
        </div>
      }
      bodyClassName="html-node-content"
    >
      <div className="html-node-section">
        <p className="html-node-section-label">Source</p>
        <textarea
          className="html-node-source"
          style={{ height: sourceHeightPx }}
          value={sourceText}
          onChange={(event) => setSourceText(event.target.value)}
          spellCheck={false}
        />
      </div>
      {/* R6.3: the draggable split handle - `nodrag` keeps React Flow's
          own node-drag gesture from hijacking this pointer-down (same
          convention as GroupNodeView's label input / ChartNodeView's
          toolbar). Purely a resize handle for the Source pane above it;
          the Preview pane below takes up whatever's left of
          HTML_SPLIT_TOTAL_PX. */}
      <div
        className="html-node-splitter nodrag"
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize source and preview panes"
        onPointerDown={onSplitterPointerDown}
      />
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
            access to this app's origin/storage/parent DOM via any DOM API -
            parent.location/top.location correctly throw SecurityError), no
            allow-popups, no allow-top-navigation, no allow-forms, no
            allow-modals. This does NOT by itself stop document.baseURI from
            reporting this app's own URL, token fragment included - that's a
            distinct HTML-spec mechanism (the srcdoc "fallback base url"),
            unrelated to the sandbox's origin isolation, which is why
            buildSandboxedHtmlDocument's own wrapper additionally sets an
            explicit <base> tag (see its doc comment) rather than relying on
            sandbox alone to keep the token out of this frame's reach. srcDoc
            (never a blob: URL, never `src`, never dangerouslySetInnerHTML)
            is the only content-delivery path, and it only ever holds
            buildSandboxedHtmlDocument's output. */}
        <iframe
          className="html-node-preview"
          style={{ height: previewHeightPx }}
          sandbox="allow-scripts"
          srcDoc={renderedDoc}
          title="HTML preview"
        />
      </div>
    </NodeShell>
  );
}

/** ADR-011 stage 11.1: every prop this view actually reads, compared - it
 * never destructures `id`, so it is intentionally absent (this instance never
 * receives a changed `id` without React Flow remounting it under a new key
 * anyway). `htmlContent` and `htmlSplitterState` are intentionally OMITTED:
 * each seeds local state ONCE via a `useState` initializer on mount
 * (`sourceText`/`renderedDoc` and `splitterValue` respectively) and is never
 * read again afterward by any JSX, effect, or event-handler closure - so
 * comparing them would only cause spurious re-renders that produce
 * byte-identical output, never fix a missed one (same reasoning
 * WebResearchNodeView's own comparator applies to researchActiveSourceId).
 * Everything else - `isCollapsed`, and every callback, including
 * `onSplitterChange`, which IS captured fresh into a real event-handler
 * closure on every render (onSplitterPointerDown's onPointerUp) and so must
 * be compared, not just fields that show up directly in JSX - is a primitive
 * or stable callback reference, so `===` is correct for all of them. */
function htmlNodeDataAreEqual(prev: HtmlNodeData, next: HtmlNodeData): boolean {
  return (
    prev.isCollapsed === next.isCollapsed &&
    prev.onToggleCollapse === next.onToggleCollapse &&
    prev.onDelete === next.onDelete &&
    prev.onSplitterChange === next.onSplitterChange
  );
}

function htmlNodePropsAreEqual(
  prev: Readonly<NodeProps<HtmlFlowNode>>,
  next: Readonly<NodeProps<HtmlFlowNode>>,
): boolean {
  return prev.selected === next.selected && htmlNodeDataAreEqual(prev.data, next.data);
}

export const HtmlNodeView = memo(HtmlNodeViewImpl, htmlNodePropsAreEqual);
