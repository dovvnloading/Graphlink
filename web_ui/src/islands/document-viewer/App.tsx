import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { DocumentViewerBridge, BridgeRejection, createDocumentViewerBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

function App() {
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const [content, setContent] = useState("");
  const bridgeRef = useRef<DocumentViewerBridge | null>(null);

  useEffect(() => {
    const bridge = createDocumentViewerBridge((state) => setContent(state.content), setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  if (rejection) {
    return (
      <BridgeErrorState
        title="Document View is unavailable"
        rejection={rejection}
        className="document-viewer-shell bridge-error"
      />
    );
  }

  return (
    <div className="document-viewer-shell">
      <div className="document-viewer-header">
        <h1 className="document-viewer-title">Document View</h1>
        <button
          type="button"
          className="document-viewer-close-btn"
          onClick={() => bridgeRef.current?.close()}
        >
          Close
        </button>
      </div>
      <div className="document-viewer-scroll-area">
        {content.trim().length > 0 ? (
          <div className="document-viewer-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
              {content}
            </ReactMarkdown>
          </div>
        ) : (
          <p className="document-viewer-empty">No document content is available yet.</p>
        )}
      </div>
    </div>
  );
}

export default App;
