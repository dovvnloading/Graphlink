import { Handle, Position } from "@xyflow/react";
import type { CSSProperties, MouseEvent, ReactNode } from "react";

/**
 * ADR-012 stage 12.5: the outer wrapper every content-card node kind was
 * hand-rolling identically. Recon for this stage found the wrapper div, the
 * two `<Handle>` elements, and the `{!collapsed && <div className="scene-
 * node-body ...">}` gate were byte-for-byte the same structural JSX in 15 of
 * 16 `*NodeView.tsx` files (`GroupNodeView` is a background frame/container,
 * not a content card - no handles, no menu, a different wrapper class
 * entirely - and stays fully independent rather than being forced through
 * this shell). This is exactly the "scaffold copy" the ADR's own exit
 * criterion names ("adding a node kind needs no scaffold copy") - a new node
 * kind renders `<NodeShell>` instead of retyping this file's own contents.
 *
 * Deliberately NOT attempting to also own the header or body CONTENT: the
 * same recon found the header row genuinely diverges across kinds (an
 * inline collapse chevron for some, no collapse affordance for others, a
 * bespoke multi-button cluster for HtmlNodeView, an elaborate badge cluster
 * for ChatNodeView far past what a fixed prop shape could describe) - so
 * `header` and `children` are pre-rendered ReactNode slots, not props this
 * component interprets. That keeps the migration onto this shell a pure
 * mechanical wrapper swap for every caller: zero change to what any node
 * kind actually looks like or does.
 */
export function NodeShell({
  kindClassName,
  selected,
  collapsed,
  onContextMenu,
  header,
  bodyClassName,
  children,
  menu,
  style,
  resizer,
  onBodyDoubleClick,
}: {
  /** e.g. "artifact-node" - combined into "scene-node artifact-node". */
  kindClassName: string;
  selected: boolean;
  collapsed: boolean;
  /** Omitted entirely for the kinds with no context menu (Chart/Html/Note) -
   * `aria-haspopup="menu"` is only stamped when this is provided, matching
   * every kind's own pre-shell behavior exactly. */
  onContextMenu?: (event: MouseEvent<HTMLDivElement>) => void;
  /** The caller's own, fully-rendered `.scene-node-title` row - untouched by
   * this component, see the file's own doc for why. */
  header: ReactNode;
  /** e.g. "artifact-node-content" - combined into "scene-node-body
   * artifact-node-content", matching every kind's own pre-shell class. */
  bodyClassName: string;
  /** Gated by `!collapsed` internally - callers no longer write that
   * conditional themselves. */
  children?: ReactNode;
  /** The caller's own `{menuPosition && <XyzNodeMenu .../>}` result -
   * rendered as-is, last, matching every kind's own pre-shell placement. */
  menu?: ReactNode;
  /** Inline style merged onto the outer wrapper div - only ChartNodeView
   * (NodeResizer-driven 100%/100% sizing) and NoteNodeView (per-note
   * background/border color) need this; every other kind omits it and gets
   * no `style` attribute at all, matching their pre-shell markup exactly. */
  style?: CSSProperties;
  /** Rendered first, before the target `<Handle>` - the one slot
   * ChartNodeView needs for its own `<NodeResizer>` element, which xyflow
   * requires living directly inside the node's own wrapper div. Omitted by
   * every other kind. */
  resizer?: ReactNode;
  /** NoteNodeView's double-click-to-edit trigger - lives on the BODY div
   * specifically (not the outer wrapper), matching its pre-shell markup.
   * Omitted by every other kind. */
  onBodyDoubleClick?: (event: MouseEvent<HTMLDivElement>) => void;
}) {
  return (
    <div
      className={`scene-node ${kindClassName}${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}
      style={style}
      onContextMenu={onContextMenu}
      {...(onContextMenu ? { "aria-haspopup": "menu" as const } : {})}
    >
      {resizer}
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      {header}
      {!collapsed && (
        <div className={`scene-node-body ${bodyClassName}`} onDoubleClick={onBodyDoubleClick}>
          {children}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menu}
    </div>
  );
}
