import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";
import { CodeExecutionApprovalPanel } from "./CodeExecutionApprovalPanel";

/**
 * The Py-Coder node (Qt-removal plan R5.4) - the Py-Coder plugin's React
 * card. Same overall shell as every plugin-node sibling (ArtifactNodeView/
 * GitlinkNodeView): collapse/expand OR-ed with LOD, a card menu with
 * outside-click/Escape dismiss, the shared react-markdown + remarkGfm +
 * rehypeHighlight pipeline, no dock-to-parent action.
 *
 * Mode + single input economy: the AI-driven/Manual toggle commits
 * IMMEDIATELY on click via data.onSetMode (backend/canvas.py registers a
 * dedicated setPyCoderMode intent for it, unlike scope_mode on the Gitlink
 * node, which has no such intent and only ever rides along a later call) -
 * but the actual prompt-or-code text lives in ONE local textarea, held in
 * component state until Run is clicked, then passed directly as
 * data.onRun(inputText)'s argument. This mirrors Artifact's/Gitlink's own
 * "instruction input is a local draft, never separately mirrored via its own
 * setter intent" economy - backend/canvas.py's start_pycoder_run docstring
 * confirms the SAME input_text lands in pycoder_prompt or pycoder_code
 * purely depending on the CURRENT server-side mode at dispatch time, so this
 * view does not need two separate draft fields for the two modes. The draft
 * is seeded ONCE from whichever field the initial mode reads (never
 * re-synced afterward - same non-clobbering posture GitlinkNodeView's own
 * Setup-tab fields document) and is NOT cleared after Run, since (unlike
 * Artifact's one-shot chat instruction) a Py-Coder prompt/code is something
 * you commonly re-run with small tweaks - the same posture Gitlink's own
 * task-prompt field takes for Generate Change Set.
 *
 * Approval: manual mode is deliberately ungated server-side (per backend/
 * agents.py's own docstring: "clicking Run *is* the approval" when the user
 * authored the code themselves) - so pycoderAwaitingApproval simply never
 * becomes true in manual mode. This view has no mode-specific conditional
 * for that; it just renders <CodeExecutionApprovalPanel> whenever the flag
 * is true, exactly like every other data-driven condition here.
 *
 * No live-streaming pane here, unlike CodeSandboxNodeView's terminal - the
 * Py-Coder REPL has no equivalent server-side emit mechanism (see
 * CodeSandboxNodeView's own module doc for the real asymmetry this reflects,
 * not an oversight in this file).
 */

export interface PyCoderNodeData extends Record<string, unknown> {
  pycoderMode: string; // "ai_driven" | "manual"
  pycoderPrompt: string;
  pycoderCode: string;
  pycoderOutput: string;
  pycoderAnalysis: string;
  pycoderLastRunFailed: boolean;
  pycoderAwaitingApproval: boolean;
  pycoderError: string;
  isCollapsed: boolean;
  pendingRequestId: string | null;
  onSetMode: (mode: string) => void;
  onRun: (inputText: string) => void;
  onCancel: () => void;
  onApprove: () => void;
  onDeny: () => void;
  onToggleCollapse: () => void;
  onDelete: () => void;
}

export type PyCoderFlowNode = Node<PyCoderNodeData, "pycoder">;

interface MenuPosition {
  x: number;
  y: number;
}

/** Same outside-click/Escape dismiss pattern every sibling node menu uses
 * (ChatNodeMenu/ArtifactNodeMenu/GitlinkNodeMenu/...). */
function useMenuDismiss(menuRef: React.RefObject<HTMLDivElement | null>, onClose: () => void) {
  useEffect(() => {
    function onPointerDown(event: PointerEvent) {
      // globalThis.Node - the DOM interface, not @xyflow/react's Node (the
      // type-only import above shadows the bare name for casts like this).
      if (!menuRef.current?.contains(event.target as globalThis.Node)) onClose();
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [menuRef, onClose]);
}

// -- card-level menu -------------------------------------------------------

function PyCoderNodeMenu({
  position,
  isCollapsed,
  onToggleCollapse,
  onDelete,
  onClose,
}: {
  position: MenuPosition;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  useMenuDismiss(menuRef, onClose);

  return (
    <div
      ref={menuRef}
      className="chat-node-menu"
      style={{ position: "fixed", left: position.x, top: position.y }}
      role="menu"
    >
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onToggleCollapse();
          onClose();
        }}
      >
        {isCollapsed ? "Expand" : "Collapse"}
      </button>
      <button
        type="button"
        role="menuitem"
        className="chat-node-menu-danger"
        onClick={() => {
          onDelete();
          onClose();
        }}
      >
        Delete Node
      </button>
    </div>
  );
}

// -- helpers ----------------------------------------------------------------

/** Wraps raw text in an untagged markdown fenced code block so ReactMarkdown
 * can render it as inert, monospaced text with zero syntax-highlighting
 * guesswork - used for the Output pane (arbitrary program stdout/stderr is
 * not Python source, so it is deliberately NOT tagged ```python the way the
 * Code pane below is). */
function toPlainFence(text: string): string {
  return "```\n" + text + "\n```";
}

function toPythonFence(code: string): string {
  return "```python\n" + code + "\n```";
}

// -- view ----------------------------------------------------------------

export function PyCoderNodeView({ id, data, selected }: NodeProps<PyCoderFlowNode>) {
  const zoom = useStore((s) => s.transform[2]);
  const lodCollapsed = zoom < LOD_ZOOM_THRESHOLD;
  const collapsed = data.isCollapsed || lodCollapsed;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  const isManual = data.pycoderMode === "manual";

  // Single local draft, seeded ONCE from whichever field the initial mode
  // reads and never re-synced afterward (see module doc) - meaning switching
  // modes mid-type does not swap or clear whatever is currently in the box,
  // by design (there is exactly one input area here, not two).
  const [inputDraft, setInputDraft] = useState(isManual ? data.pycoderCode : data.pycoderPrompt);

  const busy = !!data.pendingRequestId;

  function runNow() {
    const text = inputDraft.trim();
    if (!text) return;
    data.onRun(text);
  }

  // Disables Approve/Deny for the brief window between a click and the next
  // scene snapshot reflecting it (preventing a double-fire) - reset the
  // instant a FRESH approval request starts (awaitingApproval flipping to
  // true again means a new, unrelated approval cycle). Adjusted directly
  // during render rather than via a useEffect + setState (React's own
  // documented "adjusting state when a prop changes" pattern) - this is a
  // derived reset keyed on a prop transition, not a subscription to an
  // external system, so doing it here avoids an extra
  // render-then-effect-then-render round trip.
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [awaitingApprovalSeen, setAwaitingApprovalSeen] = useState(data.pycoderAwaitingApproval);
  if (data.pycoderAwaitingApproval !== awaitingApprovalSeen) {
    setAwaitingApprovalSeen(data.pycoderAwaitingApproval);
    if (data.pycoderAwaitingApproval) setApprovalBusy(false);
  }

  function handleApprove() {
    setApprovalBusy(true);
    data.onApprove();
  }

  function handleDeny() {
    setApprovalBusy(true);
    data.onDeny();
  }

  return (
    <div
      className={`scene-node pycoder-node${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
    >
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-title chat-node-role">
        <span>Py-Coder</span>
        <button
          type="button"
          className="chat-node-collapse-btn"
          aria-label={data.isCollapsed ? "Expand" : "Collapse"}
          onClick={data.onToggleCollapse}
        >
          {data.isCollapsed ? "▸" : "▾"}
        </button>
      </div>
      {!collapsed && (
        <div className="scene-node-body pycoder-node-content">
          <div className="pycoder-node-mode-toggle" role="group" aria-label="Mode">
            <button
              type="button"
              aria-pressed={!isManual}
              className={`pycoder-node-mode-btn${!isManual ? " active" : ""}`}
              onClick={() => data.onSetMode("ai_driven")}
            >
              AI-Driven
            </button>
            <button
              type="button"
              aria-pressed={isManual}
              className={`pycoder-node-mode-btn${isManual ? " active" : ""}`}
              onClick={() => data.onSetMode("manual")}
            >
              Manual
            </button>
          </div>

          <textarea
            className="pycoder-node-input"
            value={inputDraft}
            onChange={(event) => setInputDraft(event.target.value)}
            placeholder={isManual ? "Write Python code…" : "Describe what the code should do…"}
            aria-label={isManual ? "Code" : "Prompt"}
            rows={4}
            spellCheck={!isManual}
          />

          <div className="pycoder-node-run-row">
            <button type="button" disabled={!inputDraft.trim() || busy} onClick={runNow}>
              Run
            </button>
            {data.pendingRequestId && (
              <button type="button" onClick={() => data.onCancel()} title="Cancel Py-Coder request">
                Cancel
              </button>
            )}
          </div>

          {data.pycoderError && (
            <div className="pycoder-node-banner-error" role="alert">
              {data.pycoderError}
            </div>
          )}

          {data.pycoderLastRunFailed && (
            <p className="pycoder-node-failed-badge">Last run failed - result may still be repaired code.</p>
          )}

          {data.pycoderCode && (
            <div className="pycoder-node-section">
              <span className="pycoder-node-section-label">Code</span>
              <div className="chat-node-content pycoder-node-code">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                  {toPythonFence(data.pycoderCode)}
                </ReactMarkdown>
              </div>
            </div>
          )}

          {data.pycoderOutput && (
            <div className="pycoder-node-section">
              <span className="pycoder-node-section-label">Output</span>
              <div className="chat-node-content pycoder-node-output">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                  {toPlainFence(data.pycoderOutput)}
                </ReactMarkdown>
              </div>
            </div>
          )}

          {data.pycoderAnalysis && (
            <div className="pycoder-node-section">
              <span className="pycoder-node-section-label">Analysis</span>
              <div className="chat-node-content pycoder-node-analysis">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                  {data.pycoderAnalysis}
                </ReactMarkdown>
              </div>
            </div>
          )}

          <CodeExecutionApprovalPanel
            nodeId={id}
            kind="pycoder"
            code={data.pycoderCode}
            awaitingApproval={data.pycoderAwaitingApproval}
            busy={approvalBusy}
            onApprove={handleApprove}
            onDeny={handleDeny}
          />
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menuPosition && (
        <PyCoderNodeMenu
          position={menuPosition}
          isCollapsed={data.isCollapsed}
          onToggleCollapse={data.onToggleCollapse}
          onDelete={data.onDelete}
          onClose={() => setMenuPosition(null)}
        />
      )}
    </div>
  );
}
