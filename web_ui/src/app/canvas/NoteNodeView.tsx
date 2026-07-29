import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { GroupColorPicker, NOTE_SYSTEM_PROMPT_BORDER_COLOR } from "./GroupColorPicker";

/**
 * The note node (Qt-removal plan R6.1) - the free-floating markdown sticky
 * note the legacy canvas placed directly (never a branch-point child - see
 * backend/canvas.py's add_note docstring). UNLIKE every R3+ content kind,
 * it renders NO Handle-driven collapse/LOD posture of its own and has no
 * fixed width class - size is entirely content-driven (grows with its
 * markdown), the one deliberate exception to every other scene-node's
 * min-width-plus-scroll convention.
 *
 * It DOES still render target/source Handle endpoints (unlike its "never a
 * branch-point child" creation posture might suggest): the "System Prompt"
 * plugin (backend/plugins.py) creates a note via add_note(is_system_prompt=
 * True) and then explicitly connects note -> branch-root
 * (SceneDocument.connect), so a note can genuinely be a real edge endpoint
 * once created - the Handle elements are what let that edge draw a visible
 * connector line, same as every other kind.
 *
 * Real: render (markdown body, same react-markdown + rehype-highlight
 * pipeline every other node kind pulls in), double-click-to-edit (plain
 * textarea, native browser undo - no custom undo/redo stack, confirmed as an
 * accepted simplification), color popover (shared GroupColorPicker), delete.
 * isSystemPrompt gets a dashed border + a small gear badge in the header;
 * isSummaryNote gets a small "grouped items" badge (no border change) - both
 * badges are purely presentational, neither is user-togglable from this
 * view (they are set once at creation, by the plugin/creation path that made
 * the note). isBranchComparison (ADR-002 Workstream 1, "Compare Branches")
 * gets a third badge the same way - a distinct icon/tooltip from
 * isSummaryNote's, since it is a different feature (see backend/canvas.py's
 * SceneNode.is_branch_comparison for why they aren't the same flag), showing
 * how many source branches compareSourceNodeIds records.
 */

export interface NoteNodeData extends Record<string, unknown> {
  content: string;
  color: string | null;
  headerColor: string | null;
  isSystemPrompt: boolean;
  isSummaryNote: boolean;
  isBranchComparison: boolean;
  compareSourceNodeIds: string[];
  onSetContent: (content: string) => void;
  onSetColor: (color: string | null, headerColor: string | null) => void;
  onDelete: () => void;
}

export type NoteFlowNode = Node<NoteNodeData, "note">;

export function NoteNodeView({ data, selected }: NodeProps<NoteFlowNode>) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(data.content);
  // Suppresses the redundant onBlur commit that fires when Escape/Enter
  // programmatically unmounts the textarea (removing a focused element
  // triggers a native blur as a side effect) - without this, Escape's
  // "revert without committing" contract would be silently undone a tick
  // later by that same blur calling onSetContent anyway.
  const skipBlurRef = useRef(false);

  function beginEdit() {
    setDraft(data.content);
    setEditing(true);
  }

  function commit(value: string) {
    data.onSetContent(value);
    setEditing(false);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Escape") {
      // R8a (UI/UX issue list finding #16): claims Escape so the overlay
      // system's own document-level handler (overlays.tsx) - which now
      // checks event.defaultPrevented before closing anything - doesn't
      // also close an unrelated open popover behind this note. Without
      // this, Escape while editing a note with e.g. Pins open would revert
      // the note AND close Pins in the same keystroke.
      event.preventDefault();
      skipBlurRef.current = true;
      setDraft(data.content);
      setEditing(false);
    }
    // Enter is a plain newline here (multi-line markdown body) - only blur
    // commits, matching the spec's "onBlur commits ... Escape reverts"
    // contract with no Enter-to-commit shortcut for this multi-line field.
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
        "scene-node note-node" +
        (selected ? " selected" : "") +
        (data.isSystemPrompt ? " system-prompt" : "")
      }
      style={{
        backgroundColor: data.color ?? undefined,
        borderColor: data.isSystemPrompt ? NOTE_SYSTEM_PROMPT_BORDER_COLOR : undefined,
      }}
    >
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-title note-node-header" style={{ backgroundColor: data.headerColor ?? undefined }}>
        <span className="note-node-badges">
          <span>Note</span>
          {data.isSystemPrompt && (
            <span className="note-node-badge" title="System Prompt" aria-label="System Prompt">
              ⚙
            </span>
          )}
          {data.isSummaryNote && (
            <span className="note-node-badge" title="Summary Note" aria-label="Summary Note">
              ⧉
            </span>
          )}
          {data.isBranchComparison && (
            <span
              className="note-node-badge"
              title={`Branch Comparison (${data.compareSourceNodeIds.length} sources)`}
              aria-label="Branch Comparison"
            >
              ⇄
            </span>
          )}
        </span>
        <div className="note-node-controls">
          <GroupColorPicker color={data.color} headerColor={data.headerColor} onSelect={data.onSetColor} />
          <button type="button" className="note-node-delete-btn nodrag" aria-label="Delete note" onClick={data.onDelete}>
            ×
          </button>
        </div>
      </div>
      <div className="scene-node-body note-node-content" onDoubleClick={beginEdit}>
        {editing ? (
          <textarea
            className="note-node-editor nodrag"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
            onBlur={onBlur}
            autoFocus
            spellCheck={false}
          />
        ) : (
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
            {data.content}
          </ReactMarkdown>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
    </div>
  );
}
