import { NodeResizer, type Node, type NodeProps } from "@xyflow/react";
import { useRef, useState } from "react";
import { GroupColorPicker } from "./GroupColorPicker";
import { GROUP_RESIZE_MIN_HEIGHT, GROUP_RESIZE_MIN_WIDTH } from "./canvasConstants";

/**
 * The shared frame/container node view (Qt-removal plan R6.1) - one
 * component parameterized by `data.groupKind`, rather than two near-
 * duplicate files, since a frame and a container differ only in: the lock
 * toggle (frame-only - containers have no lock concept at all, see
 * backend/canvas.py's create_container docstring) and the resize handles
 * (frame-only - there is no resize_container). Both NODE_TYPES entries
 * ("frame" and "container" in SceneCanvas.tsx) point at this same component.
 *
 * Renders as a BACKGROUND DECORATION, not a content card: a colored rounded
 * box sized/positioned exactly per the node's own x/y/width/height, which
 * are ENTIRELY backend-owned (backend/canvas.py's _recompute_group_bounds -
 * this view never computes a bbox of its own, it only renders what the scene
 * snapshot says). Member nodes are ordinary independent top-level React Flow
 * nodes at their own absolute positions (NOT React Flow parentId/extent
 * children - see that same backend comment for why this is the deliberate,
 * simpler equivalent of legacy's "auto-grow, never clip" behavior) - so
 * paint order alone is what keeps this box visually behind its members:
 * SceneCanvas.tsx's toFlowNodes sets zIndex to -1 for a frame and -2 for a
 * container (container further back, matching legacy's own relative
 * ordering) for exactly that.
 *
 * Sizing: `width`/`height` are set on the FLOW NODE OBJECT itself (not just
 * inside `data`) in SceneCanvas.tsx's toFlowNodes - the documented xyflow
 * mechanism for letting <NodeResizer/> drive the node WRAPPER element's own
 * size directly. This component's own root div therefore fills that wrapper
 * (100%/100%) rather than carrying its own pixel width/height.
 *
 * Drag: a locked frame's (or any container's) own body is the intentional
 * group-drag handle - see SceneCanvas.tsx's onNodesChange for the delta
 * application to itemIds members, which now cascades recursively into any
 * member that is itself a group (container-of-container, or a frame nested
 * inside a container). An UNLOCKED frame is ALSO draggable (restored,
 * matching legacy's own "an unlocked frame can be dragged independently of
 * its members" behavior) - it just doesn't carry members along; see
 * groupDragKindOf in SceneCanvas.tsx, which gates the member cascade on
 * lock state, not draggability itself.
 *
 * Collapsed container hover preview: a simplified equivalent of legacy's
 * timer-based "ghost frame" (a rendered miniature preview of expanded
 * contents on hover, without actually expanding). This shows a lightweight
 * tooltip listing member count and kinds instead of a full content
 * render - container-only, matching legacy (frames never had this).
 */

export interface GroupNodeData extends Record<string, unknown> {
  groupKind: "frame" | "container";
  label: string;
  color: string | null;
  headerColor: string | null;
  isCollapsed: boolean;
  isLocked: boolean;
  itemIds: string[];
  memberKinds: string[];
  onSetLabel: (text: string) => void;
  onToggleCollapsed: () => void;
  onToggleLock: () => void;
  onSetColor: (color: string | null, headerColor: string | null) => void;
  onResize: (width: number, height: number) => void;
  onFitToContent: () => void;
  onUngroup: () => void;
}

export type GroupFlowNode = Node<GroupNodeData, "frame" | "container">;

export function GroupNodeView({ id, data, selected }: NodeProps<GroupFlowNode>) {
  const isFrame = data.groupKind === "frame";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(data.label);
  // Same "programmatic unmount fires a redundant blur" guard as
  // NoteNodeView's own editor - see that component's doc comment.
  const skipBlurRef = useRef(false);
  const [hovered, setHovered] = useState(false);
  const showGhostPreview = !isFrame && data.isCollapsed && hovered && data.memberKinds.length > 0;

  function beginEdit() {
    setDraft(data.label);
    setEditing(true);
  }

  function commit(value: string) {
    data.onSetLabel(value);
    setEditing(false);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      // R8a (UI/UX issue list finding #16): claims Escape so overlays.tsx's
      // own document-level handler doesn't also close an unrelated open
      // popover behind this frame/container - see NoteNodeView's identical
      // guard for the full reasoning.
      event.preventDefault();
      skipBlurRef.current = true;
      setDraft(data.label);
      setEditing(false);
    } else if (event.key === "Enter") {
      event.preventDefault();
      skipBlurRef.current = true;
      commit(draft);
    }
  }

  function onBlur() {
    if (skipBlurRef.current) {
      skipBlurRef.current = false;
      return;
    }
    commit(draft);
  }

  return (
    <div
      className={
        "group-node" +
        ` ${data.groupKind}-group-node` +
        (selected ? " selected" : "") +
        (data.isCollapsed ? " collapsed" : "")
      }
      style={{ width: "100%", height: "100%", backgroundColor: data.color ?? undefined }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {showGhostPreview && <GhostPreview memberKinds={data.memberKinds} />}
      <NodeResizer
        nodeId={id}
        isVisible={isFrame && !data.isCollapsed}
        minWidth={GROUP_RESIZE_MIN_WIDTH}
        minHeight={GROUP_RESIZE_MIN_HEIGHT}
        onResizeEnd={(_event, params) => data.onResize(params.width, params.height)}
      />
      <div
        className="group-node-header"
        style={{ backgroundColor: data.headerColor ?? undefined }}
        onDoubleClick={beginEdit}
      >
        {editing ? (
          <input
            type="text"
            className="group-node-label-input nodrag"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
            onBlur={onBlur}
            autoFocus
          />
        ) : (
          <span className="group-node-label">{data.label}</span>
        )}
        <div className="group-node-controls nodrag">
          <button type="button" className="group-node-btn" onClick={data.onToggleCollapsed}>
            {data.isCollapsed ? "Expand" : "Collapse"}
          </button>
          {isFrame && (
            <button type="button" className="group-node-btn" onClick={data.onToggleLock}>
              {data.isLocked ? "Unlock" : "Lock"}
            </button>
          )}
          {isFrame && !data.isCollapsed && (
            <button type="button" className="group-node-btn" onClick={data.onFitToContent}>
              Fit to Content
            </button>
          )}
          <GroupColorPicker color={data.color} headerColor={data.headerColor} onSelect={data.onSetColor} />
          <button type="button" className="group-node-btn group-node-ungroup-btn" onClick={data.onUngroup}>
            Ungroup
          </button>
        </div>
      </div>
    </div>
  );
}

// R6.1 follow-up: the simplified ghost-preview tooltip - member count plus
// a per-kind breakdown ("3 items: chat x2, code x1"), not a rendered
// miniature of each member's actual content the way legacy's real
// ghost-frame preview was. Kinds are counted (not listed one-by-one) since
// a container can easily hold a dozen+ members - a flat list would be
// noisier than useful at that size.
function GhostPreview({ memberKinds }: { memberKinds: string[] }) {
  const counts = new Map<string, number>();
  for (const kind of memberKinds) counts.set(kind, (counts.get(kind) ?? 0) + 1);
  const breakdown = [...counts.entries()].map(([kind, count]) => `${kind}${count > 1 ? ` x${count}` : ""}`);

  return (
    <div className="group-node-ghost-preview nodrag" role="tooltip">
      <span className="group-node-ghost-preview-count">
        {memberKinds.length} item{memberKinds.length === 1 ? "" : "s"}
      </span>
      <span className="group-node-ghost-preview-breakdown">{breakdown.join(", ")}</span>
    </div>
  );
}
