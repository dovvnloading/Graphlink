import { NodeResizer, type Node, type NodeProps } from "@xyflow/react";
import { memo, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
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
 * (100%/100%) rather than carrying its own pixel width/height. A collapsed
 * group is NOT a CSS-only illusion - backend/canvas.py's
 * _recompute_group_bounds pins group_width/group_height to the fixed
 * GROUP_COLLAPSED_WIDTH/HEIGHT (260x50) while collapsed, so the wrapper
 * really does shrink to that footprint; the pill treatment below relies on
 * that being a small, fixed, wide-and-short box.
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
 * Visual system ("Quiet Frame", R8a visual-quality pass): the box itself is
 * a near-invisible tint at rest - no permanent header bar, no full-opacity
 * border - so it reads as a boundary around its members rather than a card
 * competing with them. Identity (label + the 5 actions) lives in small
 * floating chips anchored above the top-left corner, which only fully
 * assert themselves on hover/selection. The one deliberate exception is the
 * Collapse/Expand toggle, which stays dimly visible even at rest (see
 * .group-node-collapse-btn) since it is the single action a user needs to
 * discover without first knowing to hover a near-invisible box.
 * <NodeResizer/>'s handles follow the same rule: `isVisible` is gated on
 * `hovered || selected`, never rendered unconditionally - this was the
 * literal, named complaint that triggered this pass (resize scaffolding
 * permanently exposed on every expanded frame).
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

// Kills the resizer's connecting outline entirely (dots only, no wireframe
// rectangle) - a cross-judge borrow from the design-panel review: a full
// connecting line reintroduces the "exposed scaffolding" look this pass
// exists to remove. Inline style (not a CSS class) so it wins outright over
// @xyflow/react's own resize-control stylesheet regardless of import order.
const RESIZE_HANDLE_STYLE: CSSProperties = {
  width: 6,
  height: 6,
  borderRadius: 0,
  backgroundColor: "var(--gl-palette-selection)",
  border: "1px solid var(--gl-surface-window)",
};
const RESIZE_LINE_STYLE: CSSProperties = {
  borderColor: "transparent",
};

function GroupNodeViewImpl({ id, data, selected }: NodeProps<GroupFlowNode>) {
  const isFrame = data.groupKind === "frame";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(data.label);
  // Same "programmatic unmount fires a redundant blur" guard as
  // NoteNodeView's own editor - see that component's doc comment.
  const skipBlurRef = useRef(false);
  const labelInputRef = useRef<HTMLInputElement>(null);
  const [hovered, setHovered] = useState(false);
  const showGhostPreview = !isFrame && data.isCollapsed && hovered && data.memberKinds.length > 0;
  const hasBodyColor = data.color !== null;
  const hasHeaderColor = data.headerColor !== null;
  // Expanded chips reflect ONLY an explicit header color, leaving the body
  // tint visible around them - the collapsed pill IS the whole visible
  // object, so it falls back to the body color too (same headerColor-first
  // fallback GroupColorPicker's own swatch trigger already uses).
  const chipStyle: CSSProperties | undefined = hasHeaderColor
    ? { backgroundColor: data.headerColor ?? undefined }
    : undefined;
  const pillStyle: CSSProperties | undefined =
    hasHeaderColor || hasBodyColor ? { backgroundColor: data.headerColor ?? data.color ?? undefined } : undefined;

  function beginEdit() {
    setDraft(data.label);
    setEditing(true);
  }

  // Imperative focus (instead of the JSX autoFocus prop) so entering rename
  // mode still focuses the input for a sighted keyboard user, without
  // tripping jsx-a11y/no-autofocus - the input element itself only exists
  // while `editing` is true (see the ternary below), so this fires exactly
  // once per edit session, same as autoFocus would have.
  useEffect(() => {
    if (editing) {
      labelInputRef.current?.focus();
    }
  }, [editing]);

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

  const labelNode = editing ? (
    <input
      ref={labelInputRef}
      type="text"
      className="group-node-label-input nodrag"
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onKeyDown={onKeyDown}
      onBlur={onBlur}
    />
  ) : (
    <span className="group-node-label">{data.label}</span>
  );

  // Lock/Fit cluster, then a divider, then Color/Ungroup - shared by both
  // the floating controls chip (expanded) and the pill's control row
  // (collapsed). The divider only renders for frames, since containers have
  // no left cluster (no Lock, no Fit) to separate from the right one.
  const controlsCluster = (
    <>
      {isFrame && (
        <button
          type="button"
          className="group-node-btn"
          title={data.isLocked ? "Unlock" : "Lock"}
          aria-label={data.isLocked ? "Unlock" : "Lock"}
          onClick={data.onToggleLock}
        >
          <GroupIcon name={data.isLocked ? "unlock" : "lock"} />
        </button>
      )}
      {isFrame && !data.isCollapsed && (
        <button
          type="button"
          className="group-node-btn"
          title="Fit to Content"
          aria-label="Fit to Content"
          onClick={data.onFitToContent}
        >
          <GroupIcon name="fit" />
        </button>
      )}
      {isFrame && <span className="group-node-controls-divider" aria-hidden="true" />}
      <GroupColorPicker color={data.color} headerColor={data.headerColor} onSelect={data.onSetColor} />
      <button
        type="button"
        className="group-node-btn group-node-ungroup-btn"
        title="Ungroup"
        aria-label="Ungroup"
        onClick={data.onUngroup}
      >
        <GroupIcon name="ungroup" />
      </button>
    </>
  );

  return (
    <div
      className={
        "group-node" +
        ` ${data.groupKind}-group-node` +
        (selected ? " selected" : "") +
        (data.isCollapsed ? " collapsed" : "") +
        (hasBodyColor ? " has-body-color" : "") +
        (hasHeaderColor ? " has-header-color" : "")
      }
      style={{ width: "100%", height: "100%", backgroundColor: data.color ?? undefined }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {showGhostPreview && <GhostPreview memberKinds={data.memberKinds} />}
      <NodeResizer
        nodeId={id}
        isVisible={isFrame && !data.isCollapsed && (hovered || selected)}
        minWidth={GROUP_RESIZE_MIN_WIDTH}
        minHeight={GROUP_RESIZE_MIN_HEIGHT}
        onResizeEnd={(_event, params) => data.onResize(params.width, params.height)}
        handleStyle={RESIZE_HANDLE_STYLE}
        lineStyle={RESIZE_LINE_STYLE}
      />
      {data.isCollapsed ? (
        <div
          className="group-node-header group-node-pill-header"
          style={pillStyle}
          onDoubleClick={beginEdit}
        >
          {labelNode}
          <div className="group-node-controls nodrag">
            <button
              type="button"
              className="group-node-btn"
              title="Expand"
              aria-label="Expand"
              onClick={data.onToggleCollapsed}
            >
              <GroupIcon name="expand" />
            </button>
            {controlsCluster}
          </div>
        </div>
      ) : (
        <div className="group-node-topbar">
          <div
            className="group-node-header group-node-label-chip nodrag"
            style={chipStyle}
            onDoubleClick={beginEdit}
          >
            {labelNode}
          </div>
          <div className="group-node-controls-row nodrag">
            <button
              type="button"
              className="group-node-btn group-node-collapse-btn"
              title="Collapse"
              aria-label="Collapse"
              onClick={data.onToggleCollapsed}
            >
              <GroupIcon name="collapse" />
            </button>
            <div className="group-node-controls-chip" style={chipStyle}>
              {controlsCluster}
            </div>
          </div>
        </div>
      )}
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

// R8a visual-quality pass: small hand-authored stroke icons (fill:none,
// stroke:currentColor, matching the same convention Composer.tsx's own
// Icon() already established) replacing the previous text-label buttons -
// every control is now an icon-only 22x22px hit target with the action name
// carried as aria-label/title instead of visible text, per the design-panel
// review's unanimous "icon-only, hover-reveal" recommendation.
type GroupIconName = "collapse" | "expand" | "lock" | "unlock" | "fit" | "ungroup";

const GROUP_ICON_PATHS: Record<GroupIconName, ReactNode> = {
  collapse: <path d="M4 6l4 4 4-4" />,
  expand: <path d="M4 10l4-4 4 4" />,
  lock: (
    <>
      <rect x="4" y="7" width="8" height="6" rx="1.2" />
      <path d="M6 7V5a2 2 0 0 1 4 0v2" />
    </>
  ),
  unlock: (
    <>
      <rect x="4" y="7" width="8" height="6" rx="1.2" />
      <path d="M6 7V5a2 2 0 0 1 4 0v1" />
    </>
  ),
  fit: <path d="M3 6V3h3M13 6V3h-3M3 10v3h3M13 10v3h-3" />,
  ungroup: (
    <>
      <rect x="2" y="5" width="5.5" height="5.5" rx="1" />
      <rect x="8.5" y="5" width="5.5" height="5.5" rx="1" />
    </>
  ),
};

function GroupIcon({ name }: { name: GroupIconName }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="group-node-icon">
      {GROUP_ICON_PATHS[name]}
    </svg>
  );
}

/** `memberKinds` is the one array field this view actually reads (the ghost
 * preview's item-count/breakdown) - toFlowNodes may mint a fresh array on
 * every snapshot even when the member set is unchanged, so a plain `===`
 * here would be "too tight" for every collapsed container with members. Same
 * shape-aware pattern NoteNodeView's own stringArraysEqual/WebResearchNodeView's
 * own use for their own string-array fields. */
function stringArraysEqual(a: readonly string[], b: readonly string[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

/** ADR-011 stage 11.1: every prop this view actually reads, compared.
 * `itemIds` is intentionally OMITTED: this file never reads it at all (the
 * member-drag cascade it documents in the module doc above is SceneCanvas.tsx's
 * own onNodesChange logic, not anything this component's render touches), so
 * comparing it would only cause spurious re-renders, never fix a missed one -
 * same reasoning WebResearchNodeView's own comparator applies to
 * researchActiveSourceId. `memberKinds` gets the shape-aware array compare
 * above instead of `===`; everything else is a primitive/nullable-primitive
 * or a stable callback reference. */
function groupNodeDataAreEqual(prev: GroupNodeData, next: GroupNodeData): boolean {
  return (
    prev.groupKind === next.groupKind &&
    prev.label === next.label &&
    prev.color === next.color &&
    prev.headerColor === next.headerColor &&
    prev.isCollapsed === next.isCollapsed &&
    prev.isLocked === next.isLocked &&
    stringArraysEqual(prev.memberKinds, next.memberKinds) &&
    prev.onSetLabel === next.onSetLabel &&
    prev.onToggleCollapsed === next.onToggleCollapsed &&
    prev.onToggleLock === next.onToggleLock &&
    prev.onSetColor === next.onSetColor &&
    prev.onResize === next.onResize &&
    prev.onFitToContent === next.onFitToContent &&
    prev.onUngroup === next.onUngroup
  );
}

/** `id` is read here (forwarded to `<NodeResizer nodeId={id} .../>`), unlike
 * some sibling views - so it's compared alongside `selected` and every `data`
 * field above. */
function groupNodePropsAreEqual(
  prev: Readonly<NodeProps<GroupFlowNode>>,
  next: Readonly<NodeProps<GroupFlowNode>>,
): boolean {
  return (
    prev.id === next.id &&
    prev.selected === next.selected &&
    groupNodeDataAreEqual(prev.data, next.data)
  );
}

export const GroupNodeView = memo(GroupNodeViewImpl, groupNodePropsAreEqual);
