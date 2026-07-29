import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { CHAT_SCROLL_REPORT_DEBOUNCE_MS, LOD_ZOOM_THRESHOLD } from "./canvasConstants";
import { downloadTextFile } from "./downloadTextFile";
import { NodeMenu } from "./NodeMenu";

/**
 * The chat node (Qt-removal plan R3.1/R3.2) - ChatNode's React successor:
 * a single message-bubble card, push-only (content arrives via the scene
 * document, never generated here). Real: render, collapse/expand, delete
 * (with the backend's reparent-children rule), copy. Deferred, with an
 * honest disabled+title label rather than a fake action or a silent drop
 * (an R3.4 live-drive audit found several legacy ChatNode menu items had
 * been dropped with zero acknowledgment - fixed here): Regenerate (assistant
 * nodes only, needs the R4 agent layer). One legacy item is still deliberately
 * NOT listed even as disabled: "Generate Group Summary" is itself
 * conditionally hidden in the legacy menu (only when a multi-selection
 * exists), and that precondition can't occur yet in the new stack (no
 * multi-select model) - showing it unconditionally would be a behavior
 * regression, not parity. "Reveal Docked Items" WAS in that same boat until
 * R3.13/R3.14 (ThinkingNode + generic docking): its precondition - one or
 * more docked children - can now be real (a thinking node docks via its own
 * "Dock to Parent Node" action), so it's implemented for real below, gated
 * on dockedChildren.length > 0 exactly like the legacy's own `if
 * docked_children:` guard. "Regenerate Response" is likewise no longer
 * deferred as of R4.3c: it now calls the real regenerateResponse intent,
 * still gated on !isUser (matching the legacy is_user guard). "Generate
 * Image" is likewise no longer deferred as of R4.4a: it now calls the real
 * generateImage intent, with no visibility/enablement gating beyond what
 * already existed - matches legacy's own unconditional enablement (the
 * empty-content case is caught server-side with a warning banner instead).
 * "Generate Chart" is likewise no longer deferred as of R6.2: it now opens a
 * real click-to-expand submenu (CHART_TYPE_OPTIONS below) offering the 5
 * chart types in legacy's own menu order (Bar/Line/Histogram/Pie/Sankey),
 * each dispatching the real generateChart intent with this node as the
 * parent. "Generate Key Takeaway" and "Generate Explainer Note" are
 * likewise no longer deferred as of R8a: their agents were lost with the
 * R7.6b Qt cutover and never ported, and the tooltip blaming a missing
 * agent layer had been stale since R4 - the very layer Regenerate/Image/
 * Chart already use. Both now dispatch real intents that drop the agent's
 * output into a new note beside this node (graphlink_note_agent.py).
 * "Open Document View" is likewise no longer deferred as of this change: it
 * now opens DocumentViewDialog.tsx with this node's own content
 * (frontend-only, no backend intent - the content is already client-side).
 * "Export" is
 * likewise no longer deferred as of R7.5a: it downloads the node's raw
 * content (not the rendered markdown) as a .md file via downloadTextFile -
 * frontend-only, no backend involved, since the content is already in
 * memory client-side. "Hide Other Branches" is likewise no longer deferred
 * as of R8a: it now calls the real onToggleBranchFocus callback, already
 * closed over this node's own id by SceneCanvas.tsx, which owns the actual
 * branch-isolation graph walk and per-node dimming style entirely - this
 * file's only job is flipping the button's own label between "Hide Other
 * Branches" and "Show All Branches" to match the scene-wide
 * isBranchFocusActive flag SceneCanvas.tsx also hands down.
 *
 * R6.3: the node's own scroll position within .chat-node-content (its
 * scrollable markdown body) is now restored on mount and reported
 * (debounced) on every scroll - the legacy serializer's own scroll_value
 * field, previously unmodeled here entirely, needed for R6.4/R6.5's session
 * load/save round trip.
 */

export interface ChatNodeData extends Record<string, unknown> {
  content: string;
  isUser: boolean;
  isCollapsed: boolean;
  dockedChildren: { id: string; label: string }[];
  // R6.3: the node's own persisted scroll position - restored into
  // .chat-node-content's scrollTop once on mount, then kept live-updated
  // (debounced) via onScrollChange as the user scrolls.
  chatScrollValue: number;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onUndockChild: (childId: string) => void;
  onRegenerate: () => void;
  onGenerateImage: () => void;
  onGenerateChart: (chartType: string) => void;
  onGenerateKeyTakeaway: () => void;
  onGenerateExplainerNote: () => void;
  onOpenDocumentView: () => void;
  onScrollChange: (value: number) => void;
  isBranchFocusActive: boolean;
  onToggleBranchFocus: () => void;
  // ADR-002 Workstream 1 ("Branch from here"): marks this node as the reply
  // target for the composer's NEXT send, instead of only ever continuing
  // from the current branch tip (sceneStore's replyTargetNodeId - see that
  // field's own comment). The actual fork happens server-side
  // (SceneDocument.send_message's branch_from_node_id) once the user
  // types a message and sends it; this callback only stages the pick.
  onBranchFromHere: () => void;
}

export type ChatFlowNode = Node<ChatNodeData, "chat">;

interface MenuPosition {
  x: number;
  y: number;
}

// R6.2: legacy's own Generate Chart submenu order, confirmed during recon
// (graphlink_canvas_chart_item.py / the legacy chat-node menu build) - Bar,
// Line, Histogram, Pie, Sankey. `value` is the exact lowercase chart_type
// string backend/canvas.py's generateChart intent (and
// graphlink_chart_data.py's SUPPORTED_CHART_TYPES) expects.
const CHART_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "bar", label: "Bar" },
  { value: "line", label: "Line" },
  { value: "histogram", label: "Histogram" },
  { value: "pie", label: "Pie" },
  { value: "sankey", label: "Sankey" },
];

function ChatNodeMenu({
  position,
  nodeId,
  content,
  isUser,
  isCollapsed,
  dockedChildren,
  onToggleCollapse,
  onDelete,
  onUndockChild,
  onRegenerate,
  onGenerateImage,
  onGenerateChart,
  onGenerateKeyTakeaway,
  onGenerateExplainerNote,
  onOpenDocumentView,
  isBranchFocusActive,
  onToggleBranchFocus,
  onBranchFromHere,
  onClose,
}: {
  position: MenuPosition;
  nodeId: string;
  content: string;
  isUser: boolean;
  isCollapsed: boolean;
  dockedChildren: { id: string; label: string }[];
  onToggleCollapse: () => void;
  onDelete: () => void;
  onUndockChild: (childId: string) => void;
  onRegenerate: () => void;
  onGenerateImage: () => void;
  onGenerateChart: (chartType: string) => void;
  onGenerateKeyTakeaway: () => void;
  onGenerateExplainerNote: () => void;
  onOpenDocumentView: () => void;
  isBranchFocusActive: boolean;
  onToggleBranchFocus: () => void;
  onBranchFromHere: () => void;
  onClose: () => void;
}) {
  const [chartMenuOpen, setChartMenuOpen] = useState(false);


  return (
    <NodeMenu position={position} onClose={onClose} className="chat-node-menu">
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          navigator.clipboard.writeText(content);
          onClose();
        }}
      >
        Copy Text
      </button>
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
        onClick={() => {
          downloadTextFile(content, `chat-${nodeId}.md`);
          onClose();
        }}
      >
        Export
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onToggleBranchFocus();
          onClose();
        }}
      >
        {isBranchFocusActive ? "Show All Branches" : "Hide Other Branches"}
      </button>
      {/* ADR-002 Workstream 1: stages this node as the composer's next reply
          target (sceneStore's replyTargetNodeId) - available regardless of
          isUser, since either a user message or an assistant reply is a
          valid point to fork a new branch from. The composer shows a
          "Replying to" indicator once set; the actual second-child branch
          is created server-side the moment the user sends. */}
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onBranchFromHere();
          onClose();
        }}
      >
        Branch from Here
      </button>
      {/* Real (not disabled) - matches the legacy's own `if docked_children:`
          guard exactly. One button per docked child, each undocking that
          specific child back onto the canvas via the shared setNodeDocked
          intent (docked=false). */}
      {dockedChildren.length > 0 && (
        <>
          <div className="chat-node-menu-section-label">Reveal Docked Items</div>
          {dockedChildren.map((child) => (
            <button
              key={child.id}
              type="button"
              role="menuitem"
              onClick={() => {
                onUndockChild(child.id);
                onClose();
              }}
            >
              {child.label}
            </button>
          ))}
        </>
      )}
      {/* R8a: real. Opens the shared DocumentViewDialog (frontend-only, no
          backend intent) with this node's own content - already client-side. */}
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onOpenDocumentView();
          onClose();
        }}
      >
        Open Document View
      </button>
      {/* R8a: real. Each runs its agent over THIS node's text and drops the
          result into a new note beside it - see backend/canvas.py's
          _generate_note_from_node. Fire-and-forget like Generate Image
          above: the note arrives on the next scene snapshot. */}
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onGenerateKeyTakeaway();
          onClose();
        }}
      >
        Generate Key Takeaway
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onGenerateExplainerNote();
          onClose();
        }}
      >
        Generate Explainer Note
      </button>
      {/* R6.2: a real click-to-expand submenu (not disabled) - same
          click-to-toggle popover interaction GroupColorPicker.tsx already
          established for a nested picker inside a single flat menu list,
          rather than inventing a hover-triggered flyout with no other
          precedent anywhere in this codebase. */}
      <button
        type="button"
        role="menuitem"
        aria-haspopup="true"
        aria-expanded={chartMenuOpen}
        onClick={() => setChartMenuOpen((open) => !open)}
      >
        Generate Chart
      </button>
      {chartMenuOpen && (
        <div className="chat-node-submenu" role="menu" aria-label="Chart type">
          {CHART_TYPE_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              role="menuitem"
              onClick={() => {
                onGenerateChart(option.value);
                onClose();
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onGenerateImage();
          onClose();
        }}
      >
        Generate Image
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
      {!isUser && (
        <button
          type="button"
          role="menuitem"
          onClick={() => {
            onRegenerate();
            onClose();
          }}
        >
          Regenerate Response
        </button>
      )}
    </NodeMenu>
  );
}

/** R6.3: the debounce wrapper for scroll-position reporting - same plain
 * clearTimeout/setTimeout-box-keyed-off-the-caller's-own-timerRef shape as
 * ChartNodeView.tsx's makeDebouncedChartResize / HtmlNodeView.tsx's
 * makeDebouncedSplitterReport, exported standalone for the same direct-unit-
 * testability reason. */
export function makeDebouncedScrollReport(
  timerRef: { current: ReturnType<typeof setTimeout> | null },
  onScrollChange: (value: number) => void,
  debounceMs: number = CHAT_SCROLL_REPORT_DEBOUNCE_MS,
): (value: number) => void {
  return (value) => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      onScrollChange(value);
    }, debounceMs);
  };
}

export function ChatNodeView({ id, data, selected }: NodeProps<ChatFlowNode>) {
  const zoom = useStore((s) => s.transform[2]);
  const lodCollapsed = zoom < LOD_ZOOM_THRESHOLD;
  const collapsed = data.isCollapsed || lodCollapsed;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);

  // R6.3: restore the saved scroll position once on mount (an empty dep
  // array - deliberate, not a lint oversight: re-running this on every scene
  // snapshot/re-render would fight the user's own live scrolling every time
  // an unrelated field on this node changes). scrollTimerRef carries the
  // debounce state across scroll events (see makeDebouncedScrollReport
  // above).
  const contentRef = useRef<HTMLDivElement>(null);
  const scrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (contentRef.current) contentRef.current.scrollTop = data.chatScrollValue;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(
    () => () => {
      if (scrollTimerRef.current) clearTimeout(scrollTimerRef.current);
    },
    [],
  );

  function onScroll(event: React.UIEvent<HTMLDivElement>) {
    makeDebouncedScrollReport(scrollTimerRef, data.onScrollChange)(event.currentTarget.scrollTop);
  }

  return (
    <div
      className={`scene-node chat-node${data.isUser ? " user" : " assistant"}${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
    >
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-title chat-node-role">
        <span className="chat-node-role-group">
          <span>{data.isUser ? "You" : "Assistant"}</span>
          {data.dockedChildren.length > 0 && (
            <span className="chat-node-docked-badge" title="Docked items">
              {data.dockedChildren.length}
            </span>
          )}
        </span>
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
        <div className="scene-node-body chat-node-content" ref={contentRef} onScroll={onScroll}>
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
            {data.content}
          </ReactMarkdown>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menuPosition && (
        <ChatNodeMenu
          position={menuPosition}
          nodeId={id}
          content={data.content}
          isUser={data.isUser}
          isCollapsed={data.isCollapsed}
          dockedChildren={data.dockedChildren}
          onToggleCollapse={data.onToggleCollapse}
          onDelete={data.onDelete}
          onUndockChild={data.onUndockChild}
          onRegenerate={data.onRegenerate}
          onGenerateImage={data.onGenerateImage}
          onGenerateChart={data.onGenerateChart}
          onGenerateKeyTakeaway={data.onGenerateKeyTakeaway}
          onGenerateExplainerNote={data.onGenerateExplainerNote}
          onOpenDocumentView={data.onOpenDocumentView}
          isBranchFocusActive={data.isBranchFocusActive}
          onToggleBranchFocus={data.onToggleBranchFocus}
          onBranchFromHere={data.onBranchFromHere}
          onClose={() => setMenuPosition(null)}
        />
      )}
    </div>
  );
}
