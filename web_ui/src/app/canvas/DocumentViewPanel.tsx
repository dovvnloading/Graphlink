import { useCallback, useRef, useState } from "react";
import { DocumentViewMarkdown } from "./DocumentViewMarkdown";

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
        <div className="document-view-panel-scroll chat-node-content">
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
