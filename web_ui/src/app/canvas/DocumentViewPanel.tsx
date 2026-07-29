import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

/**
 * Document View panel (Qt-removal plan R7.6b's stub, redone as a real
 * docked panel). Legacy's DocumentViewerPanel/DocumentViewerWebHost
 * (graphlink_window.py/graphlink_document_viewer_web.py, deleted in R7.6b)
 * was a permanent embedded QWidget - a fixed-500px, flush-left panel that
 * was a QHBoxLayout SIBLING of the graph view (content_layout.addWidget
 * ordering: doc_viewer_panel, then chat_view), toggled via setVisible(),
 * never a floating/modal window. The SPA's first cut of this (R7.6b) wired
 * the content into the shared centered Dialog surface instead, since that
 * was the only overlay primitive built at the time - this replaces that
 * with a real docked flex sibling of the canvas (see CanvasInner's own
 * render in SceneCanvas.tsx, which shrinks the graph view exactly like
 * legacy's QHBoxLayout did) rather than a shared Dialog/Popover tier.
 * Closing it only ever happens via its own Close button, matching legacy
 * exactly - it was never part of Qt's OverlayManager, so no scrim, no
 * Escape-to-close, no focus trap here either.
 */
export function DocumentViewPanel({
  content,
  onClose,
}: {
  content: string | null;
  onClose: () => void;
}) {
  return (
    <aside className="document-view-panel" aria-label="Document View">
      <header className="document-view-panel-header">
        <span className="document-view-panel-title">Document View</span>
        <button type="button" className="document-view-panel-close" onClick={onClose}>
          Close
        </button>
      </header>
      <div className="document-view-panel-scroll chat-node-content">
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
          {content ?? ""}
        </ReactMarkdown>
      </div>
    </aside>
  );
}
