import type { Node, NodeProps } from "@xyflow/react";
import { memo, useEffect, useState } from "react";
import type { StreamListener } from "../../lib/ws/transport";
import { CodeExecutionApprovalPanel } from "./CodeExecutionApprovalPanel";
import { CollapseToggleButton } from "./CollapseToggleButton";
import type { MenuPosition } from "./menuPosition";
import { NodeMarkdown } from "./NodeMarkdown";
import { NodeMenu } from "./NodeMenu";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";

/**
 * The Virtual Environment Runner node (Qt-removal plan R5.4, renamed under
 * ADR-002 P0 from "Execution Sandbox" - that name oversold what is actually
 * a plain OS subprocess running inside a venv, not OS-level isolation; the
 * internal kind="code_sandbox" identifier/CSS classes/WS intents are
 * UNCHANGED, only the display string moved) - the Code-Sandbox plugin's
 * React card. Same overall shell as every plugin-node sibling
 * (GitlinkNodeView): collapse/expand OR-ed with LOD, a card
 * menu with outside-click/Escape dismiss, the shared NodeMarkdown.tsx
 * renderer (node redesign stage 1), no dock-to-parent action.
 *
 * There is no mode toggle here - a Run's input_text always lands in
 * code_sandbox_prompt, and code_sandbox_code is only ever populated as the
 * OUTPUT of a prior generation (there is no manual-code entry point for
 * this kind at all - see backend/domain/graph.py's own
 * start_code_sandbox_run). So Run is enabled whenever there is EITHER a
 * non-empty prompt draft OR already-generated code to re-run - mirroring
 * the backend's own guard ("if prompt_text: regenerate ... elif not
 * current_code: refuse") rather than requiring the prompt box to be
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
 * Live terminal: VirtualEnvSandbox's subprocess-based execution has a real
 * line-emission hook (`emit_line`) - a genuine backend capability, not a
 * frontend embellishment - so this node subscribes to transport's existing
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
  codeSandboxApprovalAllowSourceBuilds: boolean;
  codeSandboxApprovalIsRepair: boolean;
  codeSandboxPrompt: string;
  codeSandboxCode: string;
  codeSandboxOutput: string;
  codeSandboxAnalysis: string;
  codeSandboxAwaitingApproval: boolean;
  codeSandboxError: string;
  isCollapsed: boolean;
  pendingRequestId: string | null;
  onSetRequirements: (requirementsText: string) => void;
  onToggleAllowSourceBuilds: (allow: boolean) => void;
  onRun: (inputText: string) => void;
  onCancel: () => void;
  onApprove: () => void;
  onDeny: () => void;
  onToggleCollapse: () => void;
  onDelete: () => void;
  subscribeStream: (requestId: string, listener: StreamListener) => () => void;
}

export type CodeSandboxFlowNode = Node<CodeSandboxNodeData, "code_sandbox">;

/** Same outside-click/Escape dismiss pattern every sibling node menu uses
 * (ChatNodeMenu/ArtifactNodeMenu/GitlinkNodeMenu/...). */
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

function toPythonFence(code: string): string {
  return "```python\n" + code + "\n```";
}

// -- view ----------------------------------------------------------------

/** ADR-011 stage 11.1: React.memo comparator - id (read into
 * CodeExecutionApprovalPanel's own nodeId), selected, and every
 * CodeSandboxNodeData field this component actually reads on every render,
 * including every callback prop. Every field on this data shape is a
 * primitive or a function - none is array/object-shaped, so a plain `===`
 * per field is correct as-is.
 *
 * codeSandboxRequirements and codeSandboxPrompt are deliberately excluded:
 * both are read ONLY as a `useState(...)` initializer (requirementsDraft/
 * promptDraft below), and a useState initializer is evaluated exactly once,
 * on the component's first mount - by the time this comparator ever runs
 * (on a RE-render), that value has already been consumed and the local draft
 * lives independently of it from then on. Comparing them here would only
 * force spurious re-renders while the user is actively typing elsewhere on
 * the scene (every snapshot re-mints the wire value these fields started
 * from), with no staleness this component could otherwise show. */
export function codeSandboxNodePropsAreEqual(
  prev: NodeProps<CodeSandboxFlowNode>,
  next: NodeProps<CodeSandboxFlowNode>,
): boolean {
  if (prev.id !== next.id || prev.selected !== next.selected) return false;
  const a = prev.data;
  const b = next.data;
  return (
    a.codeSandboxApprovalRequirements === b.codeSandboxApprovalRequirements &&
    a.codeSandboxApprovalAllowSourceBuilds === b.codeSandboxApprovalAllowSourceBuilds &&
    a.codeSandboxApprovalIsRepair === b.codeSandboxApprovalIsRepair &&
    a.codeSandboxCode === b.codeSandboxCode &&
    a.codeSandboxOutput === b.codeSandboxOutput &&
    a.codeSandboxAnalysis === b.codeSandboxAnalysis &&
    a.codeSandboxAwaitingApproval === b.codeSandboxAwaitingApproval &&
    a.codeSandboxError === b.codeSandboxError &&
    a.isCollapsed === b.isCollapsed &&
    a.pendingRequestId === b.pendingRequestId &&
    a.onSetRequirements === b.onSetRequirements &&
    a.onToggleAllowSourceBuilds === b.onToggleAllowSourceBuilds &&
    a.onRun === b.onRun &&
    a.onCancel === b.onCancel &&
    a.onApprove === b.onApprove &&
    a.onDeny === b.onDeny &&
    a.onToggleCollapse === b.onToggleCollapse &&
    a.onDelete === b.onDelete &&
    a.subscribeStream === b.subscribeStream
  );
}

export const CodeSandboxNodeView = memo(function CodeSandboxNodeView({
  id,
  data,
  selected,
}: NodeProps<CodeSandboxFlowNode>) {
  const lodCollapsed = useLodVisibility();
  const collapsed = data.isCollapsed || lodCollapsed;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);

  // -- requirements: local draft, committed only on blur/Enter -------------
  const [requirementsDraft, setRequirementsDraft] = useState(data.codeSandboxRequirements);

  function commitRequirements() {
    data.onSetRequirements(requirementsDraft.trim());
  }

  // -- prompt: single local draft, committed only via Run (see module doc
  // re: Run-enablement) --------------------------------------------------
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
  // Same render-time-adjustment posture as the streamedOutput reset above:
  // an OBSERVED false->true transition on codeSandboxAwaitingApproval, not
  // a useEffect, so the busy flag clears in the SAME render the new gate's
  // props arrive - a useEffect would paint one stale frame with the old
  // (now-wrong) button state first.
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
    <NodeShell
      kindClassName="code-sandbox-node"
      selected={!!selected}
      collapsed={collapsed}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
      header={
        <div className="scene-node-title chat-node-role">
          <span>Virtual Environment Runner</span>
          <CollapseToggleButton isCollapsed={data.isCollapsed} onToggleCollapse={data.onToggleCollapse} />
        </div>
      }
      bodyClassName="code-sandbox-node-content"
      menu={
        <>
          {/* R8a: this panel must render regardless of `collapsed` (a
              blocking approval prompt must never unmount mid-decision on
              collapse/LOD) - it self-hides via its own `if
              (!awaitingApproval) return null`. NodeShell's `children` slot is
              internally gated by `!collapsed`, so this can't live there; it's
              placed in `menu` instead, which - like `header` - NodeShell
              always renders unconditionally. CodeExecutionApprovalPanel
              itself renders through a `createPortal(..., document.body)` (see
              that file), so its position among NodeShell's props has no
              effect on the actual rendered DOM's structure, order, or
              stacking either way. */}
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
            allowSourceBuilds={data.codeSandboxApprovalAllowSourceBuilds}
            onToggleAllowSourceBuilds={data.onToggleAllowSourceBuilds}
            isRepairApproval={data.codeSandboxApprovalIsRepair}
            busy={approvalBusy}
            onApprove={handleApprove}
            onDeny={handleDeny}
          />
          {menuPosition && (
            <CodeSandboxNodeMenu
              position={menuPosition}
              isCollapsed={data.isCollapsed}
              onToggleCollapse={data.onToggleCollapse}
              onDelete={data.onDelete}
              onClose={() => setMenuPosition(null)}
            />
          )}
        </>
      }
    >
          {/* The prompt is the node's primary input - it leads. The old
              order put the requirements manifest first, which is the same
              mistake the Builder launcher made with its recipe picker:
              the qualifier before the question. */}
          <textarea
            className="code-sandbox-node-input"
            value={promptDraft}
            onChange={(event) => setPromptDraft(event.target.value)}
            placeholder="Describe what the code should do…"
            aria-label="Prompt"
            rows={2}
            spellCheck
          />

          <div className="code-sandbox-node-run-row">
            <button
              type="button"
              className="code-sandbox-node-run"
              disabled={!canRun || busy}
              onClick={runNow}
            >
              {busy ? "Running…" : "Run"}
            </button>
            {data.pendingRequestId && (
              <button
                type="button"
                className="code-sandbox-node-cancel"
                onClick={() => data.onCancel()}
                title="Cancel Virtual Environment Runner request"
              >
                Cancel
              </button>
            )}
          </div>

          {/* Dependencies fold away: a manifest is set once and then mostly
              read never - it was previously the FIRST thing on the card, an
              always-open two-row textarea that clipped its own third line.
              The summary carries the count so a folded manifest still says
              what it holds. Native <details>, so keyboard and screen-reader
              behaviour come from the platform; the textarea keeps its
              blur/Enter commit contract unchanged. */}
          <details className="code-sandbox-node-deps">
            <summary>
              Dependencies
              {(() => {
                const count = requirementsDraft
                  .split("\n")
                  .filter((line) => line.trim() && !line.trim().startsWith("#")).length;
                return count > 0 ? (
                  <span className="code-sandbox-node-deps-count">{count}</span>
                ) : null;
              })()}
            </summary>
            <textarea
              className="code-sandbox-node-requirements"
              value={requirementsDraft}
              onChange={(event) => setRequirementsDraft(event.target.value)}
              onBlur={commitRequirements}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
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
          </details>

          {data.codeSandboxError && (
            <div className="code-sandbox-node-banner-error" role="alert">
              {data.codeSandboxError}
            </div>
          )}

          {data.codeSandboxCode && (
            <div className="code-sandbox-node-section">
              {/* No "Code" label: the fence header NodeMarkdown renders
                  already says PYTHON with its own Copy button, and two
                  stacked all-caps headers naming the same pane was part of
                  what read as a form rather than a tool. */}
              <div className="chat-node-content code-sandbox-node-code">
                <NodeMarkdown content={toPythonFence(data.codeSandboxCode)} />
              </div>
            </div>
          )}

          {/* The terminal exists only once there is (or is about to be)
              output. The old card rendered a permanent "No output yet." box
              on every fresh node - an empty state for a section the user had
              not asked for yet. */}
          {(data.pendingRequestId || data.codeSandboxOutput) && (
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
                  : data.codeSandboxOutput}
              </pre>
            </div>
          )}

          {data.codeSandboxAnalysis && (
            <div className="code-sandbox-node-section">
              <span className="code-sandbox-node-section-label">Analysis</span>
              <div className="chat-node-content code-sandbox-node-analysis">
                <NodeMarkdown content={data.codeSandboxAnalysis} />
              </div>
            </div>
          )}
    </NodeShell>
  );
}, codeSandboxNodePropsAreEqual);
