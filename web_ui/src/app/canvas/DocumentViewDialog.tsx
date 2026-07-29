import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Dialog } from "../overlays/overlays";

/**
 * Document View dialog (finishes a legacy stub carried over from the Qt
 * cutover, R7.6b) - the SPA successor to graphlink_document_viewer_web.py,
 * which the Qt app deleted rather than ported. That panel showed a node's
 * full text as a read-only, scrollable document; the SPA rebuild left
 * "Open Document View" wired as a disabled menu stub with an honest tooltip
 * until this content had somewhere to render.
 *
 * Frontend-only: the content a node has to show (a chat node's own message,
 * or a conversation node's assembled transcript) already lives in this
 * browser's own scene state - there is nothing to fetch, no backend
 * involvement, no new WS intent. SceneCanvas.tsx builds the markdown string
 * and hands it down as `content`; this component only renders it.
 *
 * Reuses the shared, centered `Dialog` surface (the same category as
 * About/Help/Settings - one app-wide, single-open surface) rather than the
 * raw-createPortal pattern NodeMenu.tsx / CodeExecutionApprovalPanel.tsx
 * use, since those are anchored popups tied to a click point and this is
 * not anchored to anything on the canvas.
 *
 * The title is a fixed "Document View" rather than something per-node
 * (e.g. including the node's title) because legacy's own panel never had a
 * dynamic per-open title either - this restores the original behavior
 * rather than inventing new behavior.
 */
export function DocumentViewDialog({ content }: { content: string | null }) {
  return (
    <Dialog name="document-view" title="Document View" className="document-view-dialog">
      <div className="chat-node-content">
        <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
          {content ?? ""}
        </ReactMarkdown>
      </div>
    </Dialog>
  );
}
