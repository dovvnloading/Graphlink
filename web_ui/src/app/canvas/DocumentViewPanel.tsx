import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DocumentViewMarkdown } from "./DocumentViewMarkdown";
import { DocumentViewToc } from "./DocumentViewToc";
import { extractHeadings } from "./documentViewHeadings";

const DEFAULT_WIDTH = 500;
const MIN_WIDTH = 320;
const MAX_WIDTH = 900;

/**
 * Document View panel (Qt-removal plan R7.6b's stub, redone as a real
 * docked panel, then refined further). Legacy's DocumentViewerPanel/
 * DocumentViewerWebHost (graphlink_window.py/graphlink_document_viewer_web.py,
 * deleted in R7.6b) was a permanent embedded QWidget - a fixed-500px,
 * flush-left panel that was a QHBoxLayout SIBLING of the graph view, toggled
 * via setVisible(). This restores that shape (a real docked flex sibling -
 * see App.tsx, which owns open/close state and mounts this alongside every
 * other piece of chrome, not just the canvas) and goes further: a real
 * slide transition (so opening/closing reads as a drawer, not a jump-cut),
 * a drag-to-resize handle, a Copy button, and a source subtitle so a
 * complex graph with many nodes doesn't leave "Document View" as the only
 * clue about which node's content is on screen.
 *
 * The slide animation only ever transitions the OUTER element's width
 * (0 <-> the user's chosen width) while the INNER content is held at a
 * fixed width the whole time - if the inner content's own width tracked the
 * animating outer width, ReactMarkdown's rendered text would reflow line by
 * line for the entire 220ms transition (visibly janky); clipping a
 * constant-width inner block via the outer's overflow:hidden instead reads
 * as a real slide, the same technique most CSS-only drawer components use.
 *
 * Closing it only ever happens via its own Close button, matching legacy
 * exactly - it was never part of Qt's OverlayManager, so no scrim, no
 * Escape-to-close, no focus trap here either. (Full redesign, stage 4 of 4,
 * revisits the Escape-to-close piece specifically - see that stage's own
 * notes for why it's being added without adopting the rest of the modal
 * treatment.)
 *
 * Full redesign, stage 1 of 4 ("content rendering upgrades"): the markdown
 * body itself is now rendered by DocumentViewMarkdown.tsx (heading anchors,
 * a code-block copy button + language badge, wide-table scroll wrapper,
 * image zoom, GitHub-style callouts) rather than a bare
 * `<ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>`
 * - see that component's own doc comment for the full plugin-pipeline
 * rationale.
 *
 * Full redesign, stage 2 of 4 ("table of contents + reading progress"): a
 * DocumentViewToc.tsx outline toggle in the header (self-hidden under 2
 * headings - see its own doc comment) and a thin reading-progress bar
 * (scroll percentage through `.document-view-panel-scroll`, computed here
 * rather than in a separate component since it needs the exact same scroll
 * container the ToC's own scroll-to-heading logic needs a ref to anyway).
 * Both reset - scroll position back to the top, progress back to 0 - the
 * moment `content` changes, so switching from a long document to a
 * different (or shorter) one never starts the reader in the middle of the
 * new content or shows a stale progress percentage before the next scroll
 * event fires.
 */
export function DocumentViewPanel({
  isOpen,
  content,
  sourceLabel,
  onClose,
}: {
  isOpen: boolean;
  content: string | null;
  sourceLabel: string | null;
  onClose: () => void;
}) {
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [isResizing, setIsResizing] = useState(false);
  const dragStartRef = useRef<{ pointerX: number; startWidth: number } | null>(null);

  // Real Pointer Capture, not a window-level pointermove/pointerup pair:
  // capturing the pointer on the handle ITSELF guarantees the browser keeps
  // routing every subsequent event for that pointer ID here (even once the
  // cursor leaves the handle's own thin hit-box, or the window entirely)
  // and - critically - guarantees a pointerup/pointercancel eventually
  // fires and auto-releases capture. A manual window listener has no such
  // guarantee: if pointerup is ever missed (an interrupted drag, a
  // synthetic click that never dispatches a real mouseup), isResizing gets
  // stuck true forever with a dangling global listener, and the panel then
  // silently resizes itself in response to ANY future mouse movement
  // anywhere on the page, from any other interaction - a real bug found
  // exactly this way while live-verifying the drawer transition.
  const onResizeStart = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      dragStartRef.current = { pointerX: event.clientX, startWidth: width };
      setIsResizing(true);
      // Feature-detected, not assumed: real browsers have supported Pointer
      // Capture for years, but the DOM environment this runs in (an older
      // WebView2/browser, or a test environment like jsdom) may not.
      event.currentTarget.setPointerCapture?.(event.pointerId);
    },
    [width],
  );

  const onResizeMove = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const start = dragStartRef.current;
    if (!start) return;
    const next = start.startWidth + (event.clientX - start.pointerX);
    setWidth(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, next)));
  }, []);

  const onResizeEnd = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    dragStartRef.current = null;
    setIsResizing(false);
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }, []);

  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(() => {
    if (!content) return;
    navigator.clipboard?.writeText(content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [content]);

  // Stage 2: table of contents + reading progress. Extracted from the raw
  // markdown source (not queried from the rendered DOM) - see
  // documentViewHeadings.ts's own doc comment for why this is both simpler
  // and available before the very first paint.
  const headings = useMemo(() => extractHeadings(content ?? ""), [content]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [readingProgress, setReadingProgress] = useState(0);

  const onScroll = useCallback((event: React.UIEvent<HTMLDivElement>) => {
    const el = event.currentTarget;
    const scrollable = el.scrollHeight - el.clientHeight;
    setReadingProgress(scrollable > 0 ? Math.min(100, Math.max(0, (el.scrollTop / scrollable) * 100)) : 0);
  }, []);

  // A new document (or the panel closing and a different one opening next)
  // must never start the reader mid-scroll from whatever the PREVIOUS
  // document left scrollTop at, and the progress bar must not show a stale
  // percentage until the next real scroll event fires. Split across two
  // mechanisms, each satisfying a different lint rule this project
  // enforces: the `readingProgress` reset uses React's own recommended
  // "adjust state when a prop changes" pattern - a plain conditional
  // during render, not a useEffect, avoiding the extra
  // render-then-effect-then-rerender cascade a `useEffect([content])`
  // calling setState would cause (react-hooks/set-state-in-effect). The
  // scrollTop reset can't join that same conditional - refs may never be
  // read or written during render (react-hooks/refs) - so it stays in its
  // own plain effect below, which itself calls no setState at all.
  const [lastRenderedContent, setLastRenderedContent] = useState(content);
  if (content !== lastRenderedContent) {
    setLastRenderedContent(content);
    setReadingProgress(0);
  }

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [content]);

  return (
    <aside
      className={[
        "document-view-panel",
        isOpen ? "document-view-panel-open" : "",
        isResizing ? "document-view-panel-resizing" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      aria-label="Document View"
      aria-hidden={!isOpen}
      style={{ width: isOpen ? width : 0 }}
    >
      <div className="document-view-panel-inner" style={{ width }}>
        <header className="document-view-panel-header">
          <div className="document-view-panel-heading">
            <span className="document-view-panel-title">Document View</span>
            {sourceLabel && <span className="document-view-panel-subtitle">{sourceLabel}</span>}
          </div>
          <DocumentViewToc headings={headings} scrollContainerRef={scrollRef} />
          <button
            type="button"
            className="document-view-panel-copy"
            onClick={onCopy}
            disabled={!content}
            title="Copy content"
            aria-label="Copy content"
          >
            {copied ? "Copied" : "Copy"}
          </button>
          <button type="button" className="document-view-panel-close" onClick={onClose}>
            Close
          </button>
        </header>
        <div
          className="document-view-panel-progress"
          role="progressbar"
          aria-label="Reading progress"
          aria-valuenow={Math.round(readingProgress)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="document-view-panel-progress-fill" style={{ width: `${readingProgress}%` }} />
        </div>
        <div className="document-view-panel-scroll chat-node-content" ref={scrollRef} onScroll={onScroll}>
          <DocumentViewMarkdown content={content ?? ""} />
        </div>
      </div>
      <div
        className="document-view-panel-resize-handle"
        onPointerDown={onResizeStart}
        onPointerMove={onResizeMove}
        onPointerUp={onResizeEnd}
        onPointerCancel={onResizeEnd}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize Document View panel"
      />
    </aside>
  );
}
