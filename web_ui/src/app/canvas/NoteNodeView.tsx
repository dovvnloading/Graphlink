import { type Node, type NodeProps } from "@xyflow/react";
import { memo, useEffect, useRef, useState } from "react";
import { GroupColorPicker, NOTE_SYSTEM_PROMPT_BORDER_COLOR } from "./GroupColorPicker";
import { NodeMarkdown } from "./NodeMarkdown";
import { NodeShell } from "./NodeShell";

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
 * Real: render (markdown body, the shared NodeMarkdown.tsx renderer every
 * other node kind now uses - node redesign stage 1), double-click-to-edit (plain
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

function NoteNodeViewImpl({ data, selected }: NodeProps<NoteFlowNode>) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(data.content);
  // Suppresses the redundant onBlur commit that fires when Escape/Enter
  // programmatically unmounts the textarea (removing a focused element
  // triggers a native blur as a side effect) - without this, Escape's
  // "revert without committing" contract would be silently undone a tick
  // later by that same blur calling onSetContent anyway.
  const skipBlurRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Moves focus into the edit textarea imperatively (rather than JSX
  // autoFocus) the moment `editing` flips true - same "focus follows entry
  // into edit mode" UX as autoFocus would give, but scoped to this one
  // mount-of-editing transition instead of every mount of the component.
  useEffect(() => {
    if (editing) {
      textareaRef.current?.focus();
    }
  }, [editing]);

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
    <NodeShell
      kindClassName={"note-node" + (data.isSystemPrompt ? " system-prompt" : "")}
      selected={!!selected}
      // Notes render NO Handle-driven collapse/LOD posture of their own -
      // see this file's own module doc - so this is always false, never
      // wired to useLodVisibility.
      collapsed={false}
      style={{
        backgroundColor: data.color ?? undefined,
        borderColor: data.isSystemPrompt ? NOTE_SYSTEM_PROMPT_BORDER_COLOR : undefined,
      }}
      header={
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
      }
      bodyClassName="note-node-content"
      onBodyDoubleClick={beginEdit}
    >
      {editing ? (
        <textarea
          ref={textareaRef}
          className="note-node-editor nodrag"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          onBlur={onBlur}
          spellCheck={false}
        />
      ) : (
        // Nested wrapper (not applied to the outer scene-node-body div,
        // which is shared with the textarea above) - same established
        // pattern ArtifactBubble/ConversationBubble already use to scope
        // .chat-node-content's shared markdown-body rules to just the
        // rendered markdown, not a sibling edit control.
        <div className="chat-node-content note-node-markdown">
          <NodeMarkdown content={data.content} />
        </div>
      )}
    </NodeShell>
  );
}

/** Order-sensitive elementwise compare - `compareSourceNodeIds` is the one
 * array field on NoteNodeData, and toFlowNodes may mint a fresh array
 * instance even when its contents haven't changed, so a plain `===` here
 * would be "too tight" (defeats memoization for every branch-comparison note
 * on every unrelated snapshot). */
function stringArraysEqual(a: readonly string[], b: readonly string[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

/** ADR-011 stage 11.1: every prop this view actually reads - it never
 * destructures `id`, so it is intentionally absent here (this instance never
 * receives a changed `id` without React Flow remounting it under a new key
 * anyway). Every other `data` field is compared; `compareSourceNodeIds` gets
 * the shape-aware array compare above instead of `===`, everything else here
 * is a primitive or stable callback reference. */
function noteNodeDataAreEqual(prev: NoteNodeData, next: NoteNodeData): boolean {
  return (
    prev.content === next.content &&
    prev.color === next.color &&
    prev.headerColor === next.headerColor &&
    prev.isSystemPrompt === next.isSystemPrompt &&
    prev.isSummaryNote === next.isSummaryNote &&
    prev.isBranchComparison === next.isBranchComparison &&
    stringArraysEqual(prev.compareSourceNodeIds, next.compareSourceNodeIds) &&
    prev.onSetContent === next.onSetContent &&
    prev.onSetColor === next.onSetColor &&
    prev.onDelete === next.onDelete
  );
}

function noteNodePropsAreEqual(
  prev: Readonly<NodeProps<NoteFlowNode>>,
  next: Readonly<NodeProps<NoteFlowNode>>,
): boolean {
  return prev.selected === next.selected && noteNodeDataAreEqual(prev.data, next.data);
}

export const NoteNodeView = memo(NoteNodeViewImpl, noteNodePropsAreEqual);
