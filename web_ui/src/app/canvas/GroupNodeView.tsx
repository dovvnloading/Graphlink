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
 * SceneCanvas.tsx's toFlowNodes sets zIndex:-1 on every frame/container flow
 * node for exactly that.
 *
 * Sizing: `width`/`height` are set on the FLOW NODE OBJECT itself (not just
 * inside `data`) in SceneCanvas.tsx's toFlowNodes - the documented xyflow
 * mechanism for letting <NodeResizer/> drive the node WRAPPER element's own
 * size directly. This component's own root div therefore fills that wrapper
 * (100%/100%) rather than carrying its own pixel width/height.
 *
 * Drag: a locked frame's (or any container's) own body is the intentional
 * group-drag handle - see SceneCanvas.tsx's onNodesChange for the delta
 * application to itemIds members. An UNLOCKED frame has draggable:false set
 * on its flow node (also in toFlowNodes) - a deliberate simplification vs.
 * legacy's own "unlocked frame can still be dragged independently" behavior,
 * confirmed as not worth preserving; its position is entirely server-
 * computed from its members either way.
 */

export interface GroupNodeData extends Record<string, unknown> {
  groupKind: "frame" | "container";
  label: string;
  color: string | null;
  headerColor: string | null;
  isCollapsed: boolean;
  isLocked: boolean;
  itemIds: string[];
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
    >
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
