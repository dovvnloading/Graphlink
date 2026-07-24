import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { StreamListener } from "../../lib/ws/transport";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";
import { CodeExecutionApprovalPanel } from "./CodeExecutionApprovalPanel";

/**
 * The Execution Sandbox node (Qt-removal plan R5.4) - the Code-Sandbox
 * plugin's React card. Same overall shell as every plugin-node sibling
 * (PyCoderNodeView/GitlinkNodeView): collapse/expand OR-ed with LOD, a card
 * menu with outside-click/Escape dismiss, the shared react-markdown +
 * remarkGfm + rehypeHighlight pipeline, no dock-to-parent action.
 *
 * Unlike Py-Coder, there is no mode toggle here - backend/canvas.py's own
 * start_code_sandbox_run docstring is explicit that there is "no
 * mode-dependent field split here" for this kind: a Run's input_text always
 * lands in code_sandbox_prompt, and code_sandbox_code is only ever populated
 * as the OUTPUT of a prior generation (there is no manual-code entry point
 * for this kind at all, unlike Py-Coder). So Run is enabled whenever there is
 * EITHER a non-empty prompt draft OR already-generated code to re-run -
 * mirroring the backend's own guard ("if prompt_text: regenerate ... elif
 * not current_code: refuse") rather than requiring the prompt box to be
 * non-empty unconditionally.
 *
 * Requirements field: local draft committed via data.onSetRequirements only
 * on blur or Enter (never every keystroke) - the same local-field-commit
 * discipline GitlinkNodeView's own Local Root field established, including
 * its FIX 8 (Enter triggers `.blur()` only, never calls the commit function
 * directly in the same handler - otherwise the onBlur handler that .blur()
 * itself triggers would double-dispatch the WS intent for one keypress).
 * Shift+Enter is additionally treated as a literal newline rather than a
 * commit, since - unlike Local Root's single path string - a requirements
 * manifest is naturally multi-line (one package per line).
 *
 * Live terminal: this is the one genuine capability asymmetry against
 * Py-Coder, matching the backends' own real difference (not a frontend
 * embellishment) - VirtualEnvSandbox's subprocess-based execution has a real
 * line-emission hook (`emit_line`) its Py-Coder REPL-based counterpart has
 * no equivalent for, so ONLY this node subscribes to transport's existing
 * subscribeStream(requestId, listener) mechanism (already exercised by
 * R4.4's own chat token streaming) for its own pendingRequestId while a run
 * is in flight, falling back to the static data.codeSandboxOutput field once
 * a run completes (pendingRequestId returns to null) or on initial mount
 * with no run in flight. Rendered as plain preformatted text, NOT through
 * the markdown pipeline - raw subprocess stdout/stderr is machine output,
 * not prose or code-to-be-colorized, the same posture GitlinkNodeView's own
 * Context tab takes for its machine-generated XML body ("rendered as plain
 * preformatted text - never run through the markdown pipeline").
 */

export interface CodeSandboxNodeData extends Record<string, unknown> {
  codeSandboxRequirements: string;
  codeSandboxApprovalRequirements: string;
  codeSandboxPrompt: string;
  codeSandboxCode: string;
  codeSandboxOutput: string;
  codeSandboxAnalysis: string;
  codeSandboxAwaitingApproval: boolean;
  codeSandboxError: string;
  isCollapsed: boolean;
  pendingRequestId: string | null;
  onSetRequirements: (requirementsText: string) => void;
  onRun: (inputText: string) => void;
  onCancel: () => void;
  onApprove: () => void;
  onDeny: () => void;
  onToggleCollapse: () => void;
  onDelete: () => void;
  subscribeStream: (requestId: string, listener: StreamListener) => () => void;
}

export type CodeSandboxFlowNode = Node<CodeSandboxNodeData, "code_sandbox">;

interface MenuPosition {
  x: number;
  y: number;
}

/** Same outside-click/Escape dismiss pattern every sibling node menu uses
 * (ChatNodeMenu/ArtifactNodeMenu/GitlinkNodeMenu/PyCoderNodeMenu/...). */
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

function CodeSandboxNodeMenu({
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

function toPythonFence(code: string): string {
  return "```python\n" + code + "\n```";
}

// -- view ----------------------------------------------------------------

export function CodeSandboxNodeView({ id, data, selected }: NodeProps<CodeSandboxFlowNode>) {
  const zoom = useStore((s) => s.transform[2]);
  const lodCollapsed = zoom < LOD_ZOOM_THRESHOLD;
  const collapsed = data.isCollapsed || lodCollapsed;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);

  // -- requirements: local draft, committed only on blur/Enter -------------
  const [requirementsDraft, setRequirementsDraft] = useState(data.codeSandboxRequirements);

  function commitRequirements() {
    data.onSetRequirements(requirementsDraft.trim());
  }

  // -- prompt: single local draft, same one-shot-until-Run economy as ------
  // PyCoderNodeView's own input (see module doc re: Run-enablement).
  const [promptDraft, setPromptDraft] = useState(data.codeSandboxPrompt);

  const busy = !!data.pendingRequestId;
  const canRun = !!promptDraft.trim() || !!data.codeSandboxCode.trim();

  function runNow() {
    if (!canRun) return;
    data.onRun(promptDraft.trim());
  }

  // -- live terminal: subscribes only while a run is genuinely in flight ---
  // The buffer reset is adjusted directly during render (React's own
  // documented "adjusting state when a prop changes" pattern) rather than as
  // a synchronous setState call inside the effect below - it needs to happen
  // the INSTANT pendingRequestId changes (so a brand-new run never shows the
  // previous run's stale content even before its first delta arrives), which
  // is a derived-state reset, not a reaction to the external stream itself.
  // The effect below is left to do only what effects are for: synchronizing
  // with the external transport (subscribing/unsubscribing), calling
  // setState solely from within the async listener callback.
  const [streamedOutput, setStreamedOutput] = useState("");
  const [subscribedRequestId, setSubscribedRequestId] = useState(data.pendingRequestId);
  if (data.pendingRequestId !== subscribedRequestId) {
    setSubscribedRequestId(data.pendingRequestId);
    setStreamedOutput("");
  }

  useEffect(() => {
    const requestId = data.pendingRequestId;
    if (!requestId) return;
    const unsubscribe = data.subscribeStream(requestId, (delta, _done, reset) => {
      setStreamedOutput((current) => (reset ? delta : current + delta));
    });
    return () => unsubscribe();
    // data.subscribeStream is a fresh closure every render (see SceneCanvas's
    // toFlowNodes) - depending on it would resubscribe on every unrelated
    // re-render; data.pendingRequestId itself is the real re-subscribe key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.pendingRequestId]);

  // -- approval --------------------------------------------------------------
  // Same render-time-adjustment posture as the streamedOutput reset above -
  // see PyCoderNodeView's own identical pattern for the full rationale.
  const [approvalBusy, setApprovalBusy] = useState(false);
  const [awaitingApprovalSeen, setAwaitingApprovalSeen] = useState(data.codeSandboxAwaitingApproval);
  if (data.codeSandboxAwaitingApproval !== awaitingApprovalSeen) {
    setAwaitingApprovalSeen(data.codeSandboxAwaitingApproval);
    if (data.codeSandboxAwaitingApproval) setApprovalBusy(false);
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
      className={`scene-node code-sandbox-node${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
    >
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-title chat-node-role">
        <span>Execution Sandbox</span>
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
        <div className="scene-node-body code-sandbox-node-content">
          <label className="code-sandbox-node-field-row">
            <span className="code-sandbox-node-field-label">Requirements</span>
            <textarea
              className="code-sandbox-node-requirements"
              value={requirementsDraft}
              onChange={(event) => setRequirementsDraft(event.target.value)}
              onBlur={commitRequirements}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  // Do NOT call commitRequirements() here too - .blur()
                  // below synchronously fires the onBlur={commitRequirements}
                  // handler above, so calling it directly here as well would
                  // dispatch the WS intent twice per Enter press (mirrors
                  // GitlinkNodeView's own FIX 8).
                  (event.target as HTMLTextAreaElement).blur();
                }
              }}
              placeholder={"numpy\npandas==2.2.0"}
              aria-label="Requirements"
              rows={2}
              spellCheck={false}
            />
          </label>

          <textarea
            className="code-sandbox-node-input"
            value={promptDraft}
            onChange={(event) => setPromptDraft(event.target.value)}
            placeholder="Describe what the code should do…"
            aria-label="Prompt"
            rows={3}
            spellCheck
          />

          <div className="code-sandbox-node-run-row">
            <button type="button" disabled={!canRun || busy} onClick={runNow}>
              Run
            </button>
            {data.pendingRequestId && (
              <button type="button" onClick={() => data.onCancel()} title="Cancel Execution Sandbox request">
                Cancel
              </button>
            )}
          </div>

          {data.codeSandboxError && (
            <div className="code-sandbox-node-banner-error" role="alert">
              {data.codeSandboxError}
            </div>
          )}

          {data.codeSandboxCode && (
            <div className="code-sandbox-node-section">
              <span className="code-sandbox-node-section-label">Code</span>
              <div className="chat-node-content code-sandbox-node-code">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                  {toPythonFence(data.codeSandboxCode)}
                </ReactMarkdown>
              </div>
            </div>
          )}

          <div className="code-sandbox-node-section">
            <span className="code-sandbox-node-section-label">Terminal</span>
            {/* Plain preformatted text, never the markdown pipeline - see
                module doc. While a run is in flight, shows live streamed
                deltas; once it completes (pendingRequestId back to null),
                falls back to the static, already-persisted
                codeSandboxOutput field. */}
            <pre className="code-sandbox-node-terminal">
              {data.pendingRequestId
                ? streamedOutput || "Waiting for output…"
                : data.codeSandboxOutput || "No output yet."}
            </pre>
          </div>

          {data.codeSandboxAnalysis && (
            <div className="code-sandbox-node-section">
              <span className="code-sandbox-node-section-label">Analysis</span>
              <div className="chat-node-content code-sandbox-node-analysis">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
                  {data.codeSandboxAnalysis}
                </ReactMarkdown>
              </div>
            </div>
          )}

          <CodeExecutionApprovalPanel
            nodeId={id}
            kind="code_sandbox"
            code={data.codeSandboxCode}
            awaitingApproval={data.codeSandboxAwaitingApproval}
            // R5.4 CODESANDBOX fix: the approval panel must show the FROZEN
            // manifest snapshot the pending approval actually refers to
            // (codeSandboxApprovalRequirements), NOT the live, still-editable
            // codeSandboxRequirements draft. The Requirements textarea above
            // is never disabled during a run, so the user can keep typing a
            // manifest for their NEXT run while this approval is still
            // pending - reading the live field here would show that
            // in-progress edit instead of what the paused run actually asked
            // to install (backend/canvas.py freezes this at the moment
            // code_sandbox_awaiting_approval flips true; see
            // AgentDispatcher.start_code_sandbox_run).
            requirements={data.codeSandboxApprovalRequirements}
            busy={approvalBusy}
            onApprove={handleApprove}
            onDeny={handleDeny}
          />
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menuPosition && (
        <CodeSandboxNodeMenu
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
