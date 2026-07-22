import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";

/**
 * The code node (Qt-removal plan R3.5/R3.6) - a card holding a single code
 * block (push-only, same as chat: content arrives via the scene document,
 * never generated here). Unlike ChatNodeView, code nodes have no manual
 * collapse toggle - they only ever auto-collapse on zoom (LOD), since there's
 * no per-node state worth toggling by hand. Real: render (syntax-highlighted,
 * via the same react-markdown + rehype-highlight pipeline chat nodes already
 * pull in - no new highlighter dependency), delete (generic cascade-delete;
 * code nodes are never branch points, so there's no reparent rule to honor),
 * copy. Deferred, with an honest disabled+title label rather than a silent
 * drop (an R3.4 live-drive audit found the legacy CodeNode menu's branch-
 * visibility item had been dropped with zero acknowledgment - fixed here):
 * Regenerate (R4), Export (R6), and Hide Other Branches (the legacy scene's
 * branch-visibility toggle has no backend/frontend equivalent at all yet -
 * unscoped, not owned by any R-phase).
 */

export interface CodeNodeData extends Record<string, unknown> {
  code: string;
  language: string;
  onDelete: () => void;
}

export type CodeFlowNode = Node<CodeNodeData, "code">;

interface MenuPosition {
  x: number;
  y: number;
}

/** Wraps raw code in a markdown fenced code block so ReactMarkdown +
 * rehype-highlight can syntax-highlight it for free - no Shiki/Prism/
 * CodeMirror needed, zero bundle growth over what chat nodes already ship. */
function toFencedCodeBlock(code: string, language: string): string {
  return "```" + language + "\n" + code + "\n```";
}

function CodeNodeMenu({
  position,
  code,
  onDelete,
  onClose,
}: {
  position: MenuPosition;
  code: string;
  onDelete: () => void;
  onClose: () => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);

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
  }, [onClose]);

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
          navigator.clipboard.writeText(code);
          onClose();
        }}
      >
        Copy Code
      </button>
      <button type="button" role="menuitem" disabled title="Export lands in R6">
        Export
      </button>
      <button type="button" role="menuitem" disabled title="Branch visibility isn't built yet">
        Hide Other Branches
      </button>
      <button type="button" role="menuitem" disabled title="Agent regeneration lands in R4">
        Regenerate Response
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
        Delete Code Block
      </button>
    </div>
  );
}

export function CodeNodeView({ data, selected }: NodeProps<CodeFlowNode>) {
  const zoom = useStore((s) => s.transform[2]);
  const collapsed = zoom < LOD_ZOOM_THRESHOLD;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);

  return (
    <div
      className={`scene-node code-node${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
    >
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-title code-node-language">
        <span>{data.language || "code"}</span>
      </div>
      {!collapsed && (
        <div className="scene-node-body code-node-content">
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
            {toFencedCodeBlock(data.code, data.language)}
          </ReactMarkdown>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menuPosition && (
        <CodeNodeMenu
          position={menuPosition}
          code={data.code}
          onDelete={data.onDelete}
          onClose={() => setMenuPosition(null)}
        />
      )}
    </div>
  );
}
