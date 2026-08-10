import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { memo, useState } from "react";
import { CodeExecutionApprovalPanel } from "./CodeExecutionApprovalPanel";
import type { MenuPosition } from "./menuPosition";
import { NodeMarkdown } from "./NodeMarkdown";
import { NodeMenu } from "./NodeMenu";
import { useLodVisibility } from "./useLodVisibility";

/**
 * The Py-Coder node (Qt-removal plan R5.4) - the Py-Coder plugin's React
 * card. Same overall shell as every plugin-node sibling (ArtifactNodeView/
 * GitlinkNodeView): collapse/expand OR-ed with LOD, a card menu with
 * outside-click/Escape dismiss, the shared NodeMarkdown.tsx renderer (node
 * redesign stage 1), no dock-to-parent action.
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

/** Same outside-click/Escape dismiss pattern every sibling node menu uses
 * (ChatNodeMenu/ArtifactNodeMenu/GitlinkNodeMenu/...). */
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

  return (
    <NodeMenu position={position} onClose={onClose} className="chat-node-menu">
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
    </NodeMenu>
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

function PyCoderNodeViewImpl({ id, data, selected }: NodeProps<PyCoderFlowNode>) {
  const lodCollapsed = useLodVisibility();
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
      // ADR-012 stage 12.3: keyboard-reachable via Shift+F10/ContextMenu -
      // see SceneCanvas.tsx's own stage-12.3 doc for the global handler.
      aria-haspopup="menu"
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
                <NodeMarkdown content={toPythonFence(data.pycoderCode)} />
              </div>
            </div>
          )}

          {data.pycoderOutput && (
            <div className="pycoder-node-section">
              <span className="pycoder-node-section-label">Output</span>
              <div className="chat-node-content pycoder-node-output">
                <NodeMarkdown content={toPlainFence(data.pycoderOutput)} />
              </div>
            </div>
          )}

          {data.pycoderAnalysis && (
            <div className="pycoder-node-section">
              <span className="pycoder-node-section-label">Analysis</span>
              <div className="chat-node-content pycoder-node-analysis">
                <NodeMarkdown content={data.pycoderAnalysis} />
              </div>
            </div>
          )}

        </div>
      )}
      {/* R8a: OUTSIDE the {!collapsed} gate on purpose - this is a
          blocking approval prompt, and collapsing the node or zooming
          past the LOD threshold used to unmount it mid-decision. It
          self-hides via `if (!awaitingApproval) return null`. */}
        <CodeExecutionApprovalPanel
          nodeId={id}
          kind="pycoder"
          code={data.pycoderCode}
          awaitingApproval={data.pycoderAwaitingApproval}
          busy={approvalBusy}
          onApprove={handleApprove}
          onDeny={handleDeny}
        />
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

/** ADR-011 stage 11.1: every prop this view actually reads, compared -
 * `id`/`selected` directly (id is read here, unlike some sibling views,
 * since it's forwarded to CodeExecutionApprovalPanel's nodeId prop), then
 * every field of `data`. All primitives/nullable-primitives or stable
 * callback references - PyCoderNodeData has no array/object fields, so
 * `===` is correct for every one of them. */
function pyCoderNodeDataAreEqual(prev: PyCoderNodeData, next: PyCoderNodeData): boolean {
  return (
    prev.pycoderMode === next.pycoderMode &&
    prev.pycoderPrompt === next.pycoderPrompt &&
    prev.pycoderCode === next.pycoderCode &&
    prev.pycoderOutput === next.pycoderOutput &&
    prev.pycoderAnalysis === next.pycoderAnalysis &&
    prev.pycoderLastRunFailed === next.pycoderLastRunFailed &&
    prev.pycoderAwaitingApproval === next.pycoderAwaitingApproval &&
    prev.pycoderError === next.pycoderError &&
    prev.isCollapsed === next.isCollapsed &&
    prev.pendingRequestId === next.pendingRequestId &&
    prev.onSetMode === next.onSetMode &&
    prev.onRun === next.onRun &&
    prev.onCancel === next.onCancel &&
    prev.onApprove === next.onApprove &&
    prev.onDeny === next.onDeny &&
    prev.onToggleCollapse === next.onToggleCollapse &&
    prev.onDelete === next.onDelete
  );
}

function pyCoderNodePropsAreEqual(
  prev: Readonly<NodeProps<PyCoderFlowNode>>,
  next: Readonly<NodeProps<PyCoderFlowNode>>,
): boolean {
  return (
    prev.id === next.id &&
    prev.selected === next.selected &&
    pyCoderNodeDataAreEqual(prev.data, next.data)
  );
}

export const PyCoderNodeView = memo(PyCoderNodeViewImpl, pyCoderNodePropsAreEqual);
