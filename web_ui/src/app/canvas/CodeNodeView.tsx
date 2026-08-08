import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { memo, useState } from "react";
import { downloadTextFile } from "./downloadTextFile";
import type { MenuPosition } from "./menuPosition";
import { NodeMarkdown } from "./NodeMarkdown";
import { NodeMenu } from "./NodeMenu";
import { useLodVisibility } from "./useLodVisibility";

/**
 * The code node (Qt-removal plan R3.5/R3.6) - a card holding a single code
 * block (push-only, same as chat: content arrives via the scene document,
 * never generated here). Unlike ChatNodeView, code nodes have no manual
 * collapse toggle - they only ever auto-collapse on zoom (LOD), since there's
 * no per-node state worth toggling by hand. Real: render (syntax-highlighted,
 * via the shared NodeMarkdown.tsx renderer every node kind now uses - node
 * redesign stage 1), delete (generic cascade-delete;
 * code nodes are never branch points, so there's no reparent rule to honor),
 * copy, and (as of R4.3c) Regenerate Response - conditionally rendered
 * (not merely disabled) on parentChatNodeId being non-null, matching
 * legacy's own menu-build-time `if self.node.parent_content_node:` gate.
 * "Export" is likewise no longer deferred as of R7.5a: it downloads the raw
 * code (not the fenced/highlighted markdown) as a file via downloadTextFile,
 * guessing a reasonable extension from the language field
 * (LANGUAGE_EXTENSIONS below) and falling back to .txt for anything
 * unrecognized - frontend-only, no backend involved, since the code is
 * already in memory client-side. Hide Other Branches is real as of R8a too
 * (an R3.4 live-drive audit had found the legacy CodeNode menu's branch-
 * visibility item dropped with zero acknowledgment; it stood as an honest
 * disabled+title stub until now): it calls data.onToggleBranchFocus
 * (already bound to this node's id by SceneCanvas) to toggle branch-focus
 * isolation, and its own label mirrors data.isBranchFocusActive - a
 * scene-wide flag, not a per-node one - reading "Show All Branches" when
 * active and "Hide Other Branches" otherwise, matching legacy's own
 * `"Show All Branches" if is_branch_hidden else "Hide Other Branches"`
 * swap. The actual graph algorithm and all state management live in
 * SceneCanvas.tsx, not here. Nothing left deferred in this menu.
 */

export interface CodeNodeData extends Record<string, unknown> {
  code: string;
  language: string;
  // R4.3c: parentChatNodeId is the one-hop-derived parent (see SceneCanvas's
  // toFlowNodes) - null for a parentless code node. Drives whether the
  // Regenerate Response menu item renders at all (see CodeNodeMenu below),
  // matching legacy's own menu-build-time `if self.node.parent_content_node:`
  // gate rather than merely disabling the item.
  parentChatNodeId: string | null;
  onRegenerate: () => void;
  onDelete: () => void;
  // R8a: isBranchFocusActive is scene-wide (true if branch focus is active
  // ANYWHERE in the scene, not just on this node) - it exists purely to
  // drive the Hide/Show Branches menu item's own label. onToggleBranchFocus
  // is already closed over this node's id by SceneCanvas; called with no
  // arguments here.
  isBranchFocusActive: boolean;
  onToggleBranchFocus: () => void;
}

export type CodeFlowNode = Node<CodeNodeData, "code">;

/** Wraps raw code in a markdown fenced code block so ReactMarkdown +
 * rehype-highlight can syntax-highlight it for free - no Shiki/Prism/
 * CodeMirror needed, zero bundle growth over what chat nodes already ship. */
function toFencedCodeBlock(code: string, language: string): string {
  return "```" + language + "\n" + code + "\n```";
}

/** R7.5a: a best-effort language -> file extension guess for Export's
 * download filename - browsers don't require a "correct" extension to
 * accept a download (same reasoning ImageNodeView.tsx's own filename helper
 * already documents), so an unrecognized language just falls back to .txt
 * rather than growing this list to be exhaustive. */
const LANGUAGE_EXTENSIONS: Record<string, string> = {
  python: "py",
  javascript: "js",
  typescript: "ts",
  jsx: "jsx",
  tsx: "tsx",
  json: "json",
  bash: "sh",
  shell: "sh",
  sh: "sh",
  html: "html",
  css: "css",
  markdown: "md",
  sql: "sql",
  java: "java",
  c: "c",
  cpp: "cpp",
  "c++": "cpp",
  csharp: "cs",
  go: "go",
  rust: "rs",
  yaml: "yaml",
  xml: "xml",
};

function codeFileExtension(language: string): string {
  return LANGUAGE_EXTENSIONS[language.trim().toLowerCase()] ?? "txt";
}

function CodeNodeMenu({
  position,
  nodeId,
  code,
  language,
  parentChatNodeId,
  onRegenerate,
  onDelete,
  isBranchFocusActive,
  onToggleBranchFocus,
  onClose,
}: {
  position: MenuPosition;
  nodeId: string;
  code: string;
  language: string;
  parentChatNodeId: string | null;
  onRegenerate: () => void;
  onDelete: () => void;
  isBranchFocusActive: boolean;
  onToggleBranchFocus: () => void;
  onClose: () => void;
}) {


  return (
    <NodeMenu position={position} onClose={onClose} className="chat-node-menu">
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          // Best-effort clipboard write - a failure (missing Clipboard API,
          // a denied permissions prompt, an insecure context) is swallowed
          // rather than left as an unhandled rejection, matching
          // ImageNodeView.tsx's own handleCopyImage: a menu action like this
          // should never crash the node, it should just silently not have
          // copied anything.
          navigator.clipboard.writeText(code).catch((error) => {
            console.error("[code-node] Copy Code failed:", error);
          });
          onClose();
        }}
      >
        Copy Code
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          downloadTextFile(code, `code-${nodeId}.${codeFileExtension(language)}`);
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
      {parentChatNodeId && (
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
    </NodeMenu>
  );
}

/** ADR-011 stage 11.1: React.memo comparator - id (read into nodeId for the
 * card menu's Export filename), selected, and every CodeNodeData field this
 * component (or the card menu it renders) actually reads, including every
 * callback prop. Every field here is a primitive or a function - no
 * array/object-shaped field on CodeNodeData is ever read, so a plain `===`
 * per field is correct as-is, no shape-aware compare needed. */
export function codeNodePropsAreEqual(prev: NodeProps<CodeFlowNode>, next: NodeProps<CodeFlowNode>): boolean {
  if (prev.id !== next.id || prev.selected !== next.selected) return false;
  const a = prev.data;
  const b = next.data;
  return (
    a.code === b.code &&
    a.language === b.language &&
    a.parentChatNodeId === b.parentChatNodeId &&
    a.isBranchFocusActive === b.isBranchFocusActive &&
    a.onRegenerate === b.onRegenerate &&
    a.onDelete === b.onDelete &&
    a.onToggleBranchFocus === b.onToggleBranchFocus
  );
}

export const CodeNodeView = memo(function CodeNodeView({
  id,
  data,
  selected,
}: NodeProps<CodeFlowNode>) {
  const collapsed = useLodVisibility();
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
        <div className="scene-node-body code-node-content chat-node-content">
          <NodeMarkdown content={toFencedCodeBlock(data.code, data.language)} />
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menuPosition && (
        <CodeNodeMenu
          position={menuPosition}
          nodeId={id}
          code={data.code}
          language={data.language}
          parentChatNodeId={data.parentChatNodeId}
          onRegenerate={data.onRegenerate}
          onDelete={data.onDelete}
          isBranchFocusActive={data.isBranchFocusActive}
          onToggleBranchFocus={data.onToggleBranchFocus}
          onClose={() => setMenuPosition(null)}
        />
      )}
    </div>
  );
}, codeNodePropsAreEqual);
