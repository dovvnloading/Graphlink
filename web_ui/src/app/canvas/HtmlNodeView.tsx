import type { Node, NodeProps } from "@xyflow/react";
import { memo, useEffect, useId, useMemo, useRef, useState } from "react";
import { resolveShortcut } from "../chrome/shortcuts";
import {
  HTML_SPLIT_DEFAULT,
  HTML_SPLIT_MAX,
  HTML_SPLIT_MIN,
  HTML_SPLIT_TOTAL_PX,
  HTML_SPLITTER_REPORT_DEBOUNCE_MS,
} from "./canvasConstants";
import { NodeShell } from "./NodeShell";
import { nextHtmlSplitterKeyboardValue } from "./htmlSplitterControls";
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
  // Persists the edited source (scene/setHtmlContent). Called on Render, on
  // the source textarea's blur and on the save key pressed inside it, and
  // only when the source differs from what the server holds - see
  // commitSource below.
  onSourceCommit: (content: string) => void;
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

  // Source is an editable draft. The sandbox only receives renderedSource,
  // which changes after an explicit Render or an external server update; a
  // keystroke alone cannot change or execute the iframe document. Keep the
  // wrapper memoized rather than rebuilding a potentially large srcdoc on
  // every textarea keystroke. Render also bumps the iframe key: an unchanged
  // srcDoc property is otherwise ignored by React/Chromium, so pressing
  // Render a second time would NOT restart a preview's scripts.
  const [sourceText, setSourceText] = useState(data.htmlContent);
  const [renderedSource, setRenderedSource] = useState(data.htmlContent);
  const [renderVersion, setRenderVersion] = useState(0);
  const renderedDoc = useMemo(() => buildSandboxedHtmlDocument(renderedSource), [renderedSource]);
  const previewOutOfDate = sourceText !== renderedSource;
  const sourceId = useId();
  const previewId = useId();

  // Off-viewport culling genuinely unmounts this card, so an edit that
  // never reached the backend is lost on the next remount (and on save).
  // commitSource persists it on the "the user is done with this edit"
  // moments: Render, the textarea losing focus, and the save key pressed
  // inside it (see onSourceKeyDown). Never per keystroke, so the server's
  // echo of a commit cannot race the cursor.
  //
  // `committedSource` is what this card believes the server holds once its
  // own in-flight commits land, so a blur after Render, or a Render of
  // untouched source, sends nothing. `pendingCommits` lists, oldest first,
  // the values this card sent that have not come back as data.htmlContent
  // yet.
  const [committedSource, setCommittedSource] = useState(data.htmlContent);
  const [pendingCommits, setPendingCommits] = useState<readonly string[]>([]);
  function commitSource() {
    if (sourceText === committedSource) return;
    setCommittedSource(sourceText);
    setPendingCommits((pending) => [...pending, sourceText]);
    data.onSourceCommit(sourceText);
  }

  // A new data.htmlContent is either the echo of one of this card's own
  // commits or a change the card did not make (an undo/redo of a
  // setHtmlContent, an agent's write, or the echo of a commit sent by an
  // earlier mount of this card). The echo must never touch the textarea:
  // the user may already be typing past it. Anything else is shown - the
  // card otherwise keeps displaying text the server no longer holds, and a
  // later save writes what the user is not looking at - but only when there
  // is no uncommitted typing to lose; typing over it simply wins on the
  // next commit, since committedSource now names the server's value.
  // Matching an echo drops every older pending entry with it (the scene bus
  // coalesces publishes, so an intermediate value may never be seen), and
  // a change the card did not make clears the list: publishes arrive in
  // server order, so whatever arrives after it is the server's real state.
  // Adjusted during render, React's documented "adjusting state when a prop
  // changes" pattern, so the reseed and the new prop land in one render.
  const [prevHtmlContent, setPrevHtmlContent] = useState(data.htmlContent);
  if (data.htmlContent !== prevHtmlContent) {
    setPrevHtmlContent(data.htmlContent);
    const echoIndex = pendingCommits.indexOf(data.htmlContent);
    if (echoIndex !== -1) {
      setPendingCommits(pendingCommits.slice(echoIndex + 1));
    } else {
      setPendingCommits([]);
      setCommittedSource(data.htmlContent);
      if (sourceText === committedSource) {
        setSourceText(data.htmlContent);
        setRenderedSource(data.htmlContent);
      }
    }
  }

  function renderPreview() {
    setRenderedSource(sourceText);
    // Remount even when the HTML is unchanged, so interactive previews can be
    // restarted (timers/DOM state/scripts) without editing the source.
    setRenderVersion((version) => version + 1);
    commitSource();
  }

  // Save is exempt from the while-typing shortcut gate (see shortcuts.ts),
  // so Ctrl+S here reaches App's document-level handler with no blur and no
  // Render. React's root listener runs before that document listener, so
  // committing here sends setHtmlContent ahead of saveChat and the saved
  // session holds what the user sees. The event is left to propagate.
  function onSourceKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key === "Enter" &&
      (event.ctrlKey || event.metaKey) &&
      !event.altKey && !event.shiftKey
    ) {
      event.preventDefault();
      event.stopPropagation();
      renderPreview();
      return;
    }
    if (resolveShortcut(event) === "save-chat") commitSource();
  }

  // R6.3: the Source/Preview split - restored from data.htmlSplitterState on
  // mount (see the component-level useState initializer), then dragged
  // locally via onSplitterPointerDown below. splitterTimerRef carries the
  // debounce state across drags (see makeDebouncedSplitterReport above).
  const [splitterValue, setSplitterValue] = useState(data.htmlSplitterState ?? HTML_SPLIT_DEFAULT);
  const splitterTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dragCleanupRef = useRef<(() => void) | null>(null);

  useEffect(
    () => () => {
      // A drag can outlive its node when React Flow culls it offscreen or
      // the user deletes it. Remove ALL window listeners and pending reports,
      // not just the report timer, to avoid stray state updates after unmount.
      dragCleanupRef.current?.();
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
    if (event.button !== undefined && event.button !== 0) return;
    // One active drag at a time. Ignore moves/ups from a different finger,
    // and restore the original size on pointercancel/window blur.
    dragCleanupRef.current?.();
    const startY = event.clientY;
    const pointerId = event.pointerId;
    const startValue = splitterValue;
    const latestValue = { current: startValue };
    const isOurPointer = (e: PointerEvent) =>
      pointerId == null || e.pointerId == null || e.pointerId === pointerId;

    function cleanup() {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerCancel);
      window.removeEventListener("blur", onWindowBlur);
      dragCleanupRef.current = null;
    }
    function onPointerMove(moveEvent: PointerEvent) {
      if (!isOurPointer(moveEvent)) return;
      const next = clampSplitterValue(startValue + (moveEvent.clientY - startY) / HTML_SPLIT_TOTAL_PX);
      latestValue.current = next;
      setSplitterValue(next);
    }
    function onPointerUp(upEvent: PointerEvent) {
      if (!isOurPointer(upEvent)) return;
      cleanup();
      // No network round trip for a click without movement.
      if (latestValue.current !== startValue) {
        makeDebouncedSplitterReport(splitterTimerRef, data.onSplitterChange)(latestValue.current);
      }
    }
    function onPointerCancel(cancelEvent: PointerEvent) {
      if (!isOurPointer(cancelEvent)) return;
      cleanup();
      setSplitterValue(startValue);
    }
    function onWindowBlur() {
      cleanup();
      setSplitterValue(startValue);
    }
    dragCleanupRef.current = cleanup;
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerCancel);
    window.addEventListener("blur", onWindowBlur);
  }

  function onSplitterKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const next = nextHtmlSplitterKeyboardValue(splitterValue, event.key);
    if (next === null) return;
    event.preventDefault();
    event.stopPropagation();
    if (next === splitterValue) return;
    setSplitterValue(next);
    makeDebouncedSplitterReport(splitterTimerRef, data.onSplitterChange)(next);
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
          id={sourceId}
          aria-label="HTML source"
          className="html-node-source"
          style={{ height: sourceHeightPx }}
          value={sourceText}
          onChange={(event) => setSourceText(event.target.value)}
          onBlur={commitSource}
          onKeyDown={onSourceKeyDown}
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
        tabIndex={0}
        aria-orientation="horizontal"
        aria-label="Resize source and preview panes"
        aria-controls={sourceId + " " + previewId}
        aria-valuemin={Math.round(HTML_SPLIT_MIN * 100)}
        aria-valuemax={Math.round(HTML_SPLIT_MAX * 100)}
        aria-valuenow={Math.round(splitterValue * 100)}
        aria-valuetext={Math.round(splitterValue * 100) + "% source, " +
          Math.round((1 - splitterValue) * 100) + "% preview"}
        onPointerDown={onSplitterPointerDown}
        onKeyDown={onSplitterKeyDown}
      />
      <div className="html-node-render-controls">
        <button
          type="button"
          className="html-node-render-btn"
          onClick={renderPreview}
          title="Render or restart the HTML preview (Ctrl+Enter in Source)"
        >
          Render
        </button>
        <span className={"html-node-preview-status" + (previewOutOfDate ? " stale" : "")} role="status">
          {previewOutOfDate ? "Preview out of date" : "Preview up to date"}
        </span>
      </div>
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
          key={renderVersion}
          id={previewId}
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
 * anyway). `htmlSplitterState` is intentionally OMITTED: it seeds
 * `splitterValue` ONCE via a `useState` initializer on mount and is never
 * read again afterward by any JSX, effect, or event-handler closure - so
 * comparing it would only cause spurious re-renders that produce
 * byte-identical output, never fix a missed one (same reasoning
 * WebResearchNodeView's own comparator applies to researchActiveSourceId).
 * `htmlContent` IS compared: the view reads it on every render to tell the
 * echo of its own commit (left alone, so it never fights the cursor) from a
 * change it did not make, such as an undo (shown) - see the prevHtmlContent
 * block in the component.
 * Everything else - `isCollapsed`, and every callback, including
 * `onSplitterChange` and `onSourceCommit`, which ARE captured fresh into real
 * event-handler closures on every render (onSplitterPointerDown's
 * onPointerUp; the Render button and textarea blur via commitSource) and so
 * must be compared, not just fields that show up directly in JSX - is a
 * primitive or stable callback reference, so `===` is correct for all of
 * them. */
function htmlNodeDataAreEqual(prev: HtmlNodeData, next: HtmlNodeData): boolean {
  return (
    prev.htmlContent === next.htmlContent &&
    prev.isCollapsed === next.isCollapsed &&
    prev.onToggleCollapse === next.onToggleCollapse &&
    prev.onDelete === next.onDelete &&
    prev.onSplitterChange === next.onSplitterChange &&
    prev.onSourceCommit === next.onSourceCommit
  );
}

function htmlNodePropsAreEqual(
  prev: Readonly<NodeProps<HtmlFlowNode>>,
  next: Readonly<NodeProps<HtmlFlowNode>>,
): boolean {
  return prev.selected === next.selected && htmlNodeDataAreEqual(prev.data, next.data);
}

export const HtmlNodeView = memo(HtmlNodeViewImpl, htmlNodePropsAreEqual);
