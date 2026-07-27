import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";
import { downloadTextFile } from "./downloadTextFile";
import { NodeMenu } from "./NodeMenu";

/**
 * The code node (Qt-removal plan R3.5/R3.6) - a card holding a single code
 * block (push-only, same as chat: content arrives via the scene document,
 * never generated here). Unlike ChatNodeView, code nodes have no manual
 * collapse toggle - they only ever auto-collapse on zoom (LOD), since there's
 * no per-node state worth toggling by hand. Real: render (syntax-highlighted,
 * via the same react-markdown + rehype-highlight pipeline chat nodes already
 * pull in - no new highlighter dependency), delete (generic cascade-delete;
 * code nodes are never branch points, so there's no reparent rule to honor),
 * copy, and (as of R4.3c) Regenerate Response - conditionally rendered
 * (not merely disabled) on parentChatNodeId being non-null, matching
 * legacy's own menu-build-time `if self.node.parent_content_node:` gate.
 * Deferred, with an honest disabled+title label rather than a silent
 * drop (an R3.4 live-drive audit found the legacy CodeNode menu's branch-
 * visibility item had been dropped with zero acknowledgment - fixed here):
 * Hide Other Branches (the legacy scene's
 * branch-visibility toggle has no backend/frontend equivalent at all yet -
 * unscoped, not owned by any R-phase). "Export" is likewise no longer
 * deferred as of R7.5a: it downloads the raw code (not the fenced/
 * highlighted markdown) as a file via downloadTextFile, guessing a
 * reasonable extension from the language field (LANGUAGE_EXTENSIONS below)
 * and falling back to .txt for anything unrecognized - frontend-only, no
 * backend involved, since the code is already in memory client-side.
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
  onClose,
}: {
  position: MenuPosition;
  nodeId: string;
  code: string;
  language: string;
  parentChatNodeId: string | null;
  onRegenerate: () => void;
  onDelete: () => void;
  onClose: () => void;
}) {


  return (
    <NodeMenu position={position} onClose={onClose} className="chat-node-menu">
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
      <button type="button" role="menuitem" disabled title="Branch visibility isn't built yet">
        Hide Other Branches
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

export function CodeNodeView({ id, data, selected }: NodeProps<CodeFlowNode>) {
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
          nodeId={id}
          code={data.code}
          language={data.language}
          parentChatNodeId={data.parentChatNodeId}
          onRegenerate={data.onRegenerate}
          onDelete={data.onDelete}
          onClose={() => setMenuPosition(null)}
        />
      )}
    </div>
  );
}
