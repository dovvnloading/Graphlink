import type { Node, NodeProps } from "@xyflow/react";
import { memo, useState } from "react";
import type { MenuPosition } from "./menuPosition";
import { NodeMenu } from "./NodeMenu";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";

/**
 * The document node (Qt-removal plan R3.9/R3.10) - graphlink_node_document.py's
 * React successor: an uploaded-file-attachment card (document or audio),
 * push-only same as chat/code. Unlike code, DocumentNode has a real manual
 * collapse toggle (mirrors ChatNode's LOD-OR-manual pattern). Unlike both
 * chat and code, a document node can never exist without a parent - the
 * backend's add_document_node requires parent_id.
 *
 * Real: render (metadata rows + gated content preview), collapse/expand,
 * delete, copy, and (as of R3.13's shared docked-child mechanism) dock -
 * "Dock into Parent Node" now calls the same generic setNodeDocked(id, true)
 * intent ThinkingNodeView uses; SceneCanvas.tsx's node/edge filtering and
 * ChatNodeView's badge/"Reveal Docked Items" are already kind-agnostic, so
 * no further wiring was needed beyond this file and SceneCanvas's document
 * branch. Also real as of this increment: Hide Other Branches / Show All
 * Branches - this view only proxies data.onToggleBranchFocus (already bound
 * to this node's id by SceneCanvas.tsx) and flips its own label off
 * data.isBranchFocusActive, a scene-wide flag rather than a per-node one;
 * the branch-scoping algorithm and the actual dimming style live entirely in
 * SceneCanvas.tsx. Deferred, with an honest disabled+title label rather than
 * a silently-dropped action (see the R3.7-era audit this increment is
 * following up on): Open File (needs a new backend endpoint; browsers
 * cannot open arbitrary local paths), Export (R6, document-kind only -
 * matches the legacy menu's own conditional).
 */

export interface DocumentNodeData extends Record<string, unknown> {
  title: string;
  content: string;
  /** "document" | "audio" (freeform string on the wire, same convention as
   * every other scene-node "kind"-like field). */
  attachmentKind: string;
  filePath: string;
  mimeType: string;
  durationSeconds: number | null;
  byteSize: number | null;
  /** Carried through from the backend contract (graphlink_node_document.py's
   * preview_label - the collapsed-pill subtitle / future docked-badge text
   * upstream). Not yet surfaced in this increment's render: the spec's
   * header requirement is "just a title label", and docking (the feature
   * that would consume it) is still a disabled placeholder below. Kept on
   * the data shape rather than dropped, so nothing here is a silent field
   * omission - it is simply unused by the UI *yet*. */
  previewLabel: string;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  onDock: () => void;
  onDelete: () => void;
  isBranchFocusActive: boolean;
  onToggleBranchFocus: () => void;
}

export type DocumentFlowNode = Node<DocumentNodeData, "document">;

// -- ported legacy formatting/heuristic rules (graphlink_node_document.py) --

/** Ports DocumentNode._format_byte_size() verbatim: falsy byte_size (None or
 * 0) is "Unknown"; whole bytes have no decimal; every larger unit is one
 * decimal place; TB is the terminal unit regardless of magnitude. */
export function formatByteSize(byteSize: number | null): string {
  if (!byteSize) return "Unknown";
  let size = byteSize;
  const units = ["B", "KB", "MB", "GB", "TB"];
  for (const unit of units) {
    if (size < 1024 || unit === "TB") {
      return unit === "B" ? `${Math.trunc(size)} ${unit}` : `${size.toFixed(1)} ${unit}`;
    }
    size /= 1024;
  }
  return `${Math.trunc(byteSize)} B`; // unreachable - mirrors the legacy fallback line
}

/** Ports graphlink_audio.format_duration() verbatim: H:MM:SS once an hour is
 * reached, otherwise M:SS (no leading zero on the leftmost unit). */
export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "Unknown";
  const totalSeconds = Math.max(0, Math.round(seconds));
  const hours = Math.floor(totalSeconds / 3600);
  const remainder = totalSeconds % 3600;
  const minutes = Math.floor(remainder / 60);
  const secs = remainder % 60;
  const pad2 = (n: number) => String(n).padStart(2, "0");
  return hours ? `${hours}:${pad2(minutes)}:${pad2(secs)}` : `${minutes}:${pad2(secs)}`;
}

/** Ports DocumentNode._normalize_preview_text() verbatim: strip the whole
 * string, split into lines, rstrip each line, rejoin, strip again, lowercase. */
function normalizePreviewText(value: string): string {
  const stripped = (value || "").trim();
  const lines = stripped.split(/\r\n|\r|\n/).map((line) => line.replace(/\s+$/, ""));
  return lines.join("\n").trim().toLowerCase();
}

/** Ports DocumentNode._build_audio_details() verbatim: the freshly-built
 * "what this attachment is" string the content gets compared against. */
function buildAudioDetails(fields: {
  durationSeconds: number | null;
  mimeType: string;
  byteSize: number | null;
  filePath: string;
}): string {
  const lines = ["Audio attachment"];
  if (fields.durationSeconds !== null) lines.push(`Duration: ${formatDuration(fields.durationSeconds)}`);
  if (fields.mimeType) lines.push(`Format: ${fields.mimeType}`);
  if (fields.byteSize) lines.push(`Size: ${formatByteSize(fields.byteSize)}`);
  if (fields.filePath) lines.push(`Path: ${fields.filePath}`);
  return lines.join("\n");
}

/** Ports DocumentNode._should_show_audio_preview() verbatim, including the
 * legacy-compat special case: older saved sessions persisted the metadata
 * block itself as the node's content, and that shape must still suppress
 * the preview even though today's freshly-built audio-details string might
 * not be a byte-for-byte match (e.g. a mime type the old session lacked). */
export function shouldShowAudioPreview(content: string, audioDetails: string): boolean {
  const normalizedContent = normalizePreviewText(content);
  if (!normalizedContent) return false;

  const normalizedDetails = normalizePreviewText(audioDetails);
  if (normalizedContent === normalizedDetails) return false;

  if (normalizedContent.startsWith("audio attachment") && normalizedContent.includes("duration:")) {
    return false;
  }

  return true;
}

/** Combines the legacy's two gates for whether the "Contents" preview panel
 * renders at all: content must be non-empty after trimming (true for both
 * kinds - DocumentNode._show_preview_content defaults True for "document"
 * but an empty preview_text still suppresses the panel), and for "audio"
 * kind specifically, the suppression heuristic above must also pass. */
export function shouldShowContentPreview(attachmentKind: string, content: string, audioDetails: string): boolean {
  if (!content.trim()) return false;
  if (attachmentKind !== "audio") return true;
  return shouldShowAudioPreview(content, audioDetails);
}

/** Ports DocumentNode._build_metadata_rows() verbatim: Type always first,
 * then Duration/Format/Size/Path each gated on its own field being
 * populated (falsy-checked exactly like the Python, not gated on kind). */
export function buildMetadataRows(fields: {
  attachmentKind: string;
  durationSeconds: number | null;
  mimeType: string;
  byteSize: number | null;
  filePath: string;
}): Array<{ label: string; value: string }> {
  const rows: Array<{ label: string; value: string }> = [
    { label: "Type", value: fields.attachmentKind === "audio" ? "Audio file" : "Document" },
  ];
  if (fields.durationSeconds !== null) rows.push({ label: "Duration", value: formatDuration(fields.durationSeconds) });
  if (fields.mimeType) rows.push({ label: "Format", value: fields.mimeType });
  if (fields.byteSize) rows.push({ label: "Size", value: formatByteSize(fields.byteSize) });
  if (fields.filePath) rows.push({ label: "Path", value: fields.filePath });
  return rows;
}

// -- menu --------------------------------------------------------------

function DocumentNodeMenu({
  position,
  content,
  attachmentKind,
  filePath,
  isCollapsed,
  onToggleCollapse,
  onDock,
  onDelete,
  isBranchFocusActive,
  onToggleBranchFocus,
  onClose,
}: {
  position: MenuPosition;
  content: string;
  attachmentKind: string;
  filePath: string;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  onDock: () => void;
  onDelete: () => void;
  isBranchFocusActive: boolean;
  onToggleBranchFocus: () => void;
  onClose: () => void;
}) {


  const isAudio = attachmentKind === "audio";
  // Legacy gate is `attachment_kind == "document"` (a strict allow-list), not
  // "not audio" - the two diverge for any other/malformed kind value (e.g. a
  // corrupted saved session). Keep the same strict check here rather than
  // `!isAudio`, which would show the Export placeholder for a kind the
  // legacy menu would never have added it for.
  const isDocumentKind = attachmentKind === "document";

  return (
    <NodeMenu position={position} onClose={onClose} className="chat-node-menu">
      {/* Order verified against graphlink_node_document_menu.py's own
          construction order: Copy Details, Collapse/Expand, Dock, Open File
          (conditional), separator, Export (conditional), Hide Other
          Branches, Delete. */}
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          // ADR-011 stage 11.1 (D11): a bare fire-and-forget clipboard write
          // left this promise's rejection unhandled - same fix ImageNodeView's
          // own Copy Image action already applies for its clipboard write.
          navigator.clipboard.writeText(content).catch((error: unknown) => {
            console.error("[document-node] Copy Details failed:", error);
          });
          onClose();
        }}
      >
        Copy Details
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onToggleCollapse();
          onClose();
        }}
      >
        {isCollapsed ? "Expand Attachment" : "Collapse to Pill"}
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          onDock();
          onClose();
        }}
      >
        Dock into Parent Node
      </button>
      {filePath && (
        <button
          type="button"
          role="menuitem"
          disabled
          title="Opening local files needs a new backend endpoint - browsers can't open arbitrary local paths"
        >
          Open File
        </button>
      )}
      <div className="chat-node-menu-separator" role="separator" />
      {isDocumentKind && (
        <button type="button" role="menuitem" disabled title="Document export isn't available yet">
          Export
        </button>
      )}
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
      <button
        type="button"
        role="menuitem"
        className="chat-node-menu-danger"
        onClick={() => {
          onDelete();
          onClose();
        }}
      >
        {isAudio ? "Delete Audio Attachment" : "Delete Attachment"}
      </button>
    </NodeMenu>
  );
}

// -- view ----------------------------------------------------------------

function DocumentNodeViewImpl({ data, selected }: NodeProps<DocumentFlowNode>) {
  const lodCollapsed = useLodVisibility();
  const collapsed = data.isCollapsed || lodCollapsed;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);

  const isAudio = data.attachmentKind === "audio";
  const audioDetails = buildAudioDetails({
    durationSeconds: data.durationSeconds,
    mimeType: data.mimeType,
    byteSize: data.byteSize,
    filePath: data.filePath,
  });
  const showPreview = shouldShowContentPreview(data.attachmentKind, data.content, audioDetails);
  const metadataRows = buildMetadataRows({
    attachmentKind: data.attachmentKind,
    durationSeconds: data.durationSeconds,
    mimeType: data.mimeType,
    byteSize: data.byteSize,
    filePath: data.filePath,
  });
  const fallbackTitle = isAudio ? "Audio Attachment" : "File Attachment";

  return (
    <NodeShell
      kindClassName="document-node"
      selected={!!selected}
      collapsed={collapsed}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
      header={
        <div className="scene-node-title chat-node-role">
          <span>{data.title || fallbackTitle}</span>
          <button
            type="button"
            className="chat-node-collapse-btn"
            aria-label={data.isCollapsed ? "Expand" : "Collapse"}
            onClick={data.onToggleCollapse}
          >
            {data.isCollapsed ? "▸" : "▾"}
          </button>
        </div>
      }
      bodyClassName="document-node-content"
      menu={
        menuPosition && (
          <DocumentNodeMenu
            position={menuPosition}
            content={data.content}
            attachmentKind={data.attachmentKind}
            filePath={data.filePath}
            isCollapsed={data.isCollapsed}
            onToggleCollapse={data.onToggleCollapse}
            onDock={data.onDock}
            onDelete={data.onDelete}
            isBranchFocusActive={data.isBranchFocusActive}
            onToggleBranchFocus={data.onToggleBranchFocus}
            onClose={() => setMenuPosition(null)}
          />
        )
      }
    >
      <dl className="document-node-metadata">
        {metadataRows.map((row) => (
          <div className="document-node-metadata-row" key={row.label}>
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>
      {showPreview && (
        <div className="document-node-preview">
          <p className="document-node-preview-label">Contents</p>
          <pre className="document-node-preview-text">{data.content.trim()}</pre>
        </div>
      )}
    </NodeShell>
  );
}

/** ADR-011 stage 11.1: every prop this view actually reads, compared - it
 * never destructures `id`, so it is intentionally absent (this instance never
 * receives a changed `id` without React Flow remounting it under a new key
 * anyway). Every `data` field is a primitive/nullable-primitive or a stable
 * callback reference (no array/object fields on DocumentNodeData), so `===`
 * is correct throughout. `previewLabel` is intentionally OMITTED: per this
 * file's own module doc, it is "not yet surfaced in this increment's
 * render" - this view never reads `data.previewLabel` anywhere, so comparing
 * it would only cause spurious re-renders, never fix a missed one (same
 * reasoning WebResearchNodeView's own comparator applies to
 * researchActiveSourceId). */
function documentNodeDataAreEqual(prev: DocumentNodeData, next: DocumentNodeData): boolean {
  return (
    prev.title === next.title &&
    prev.content === next.content &&
    prev.attachmentKind === next.attachmentKind &&
    prev.filePath === next.filePath &&
    prev.mimeType === next.mimeType &&
    prev.durationSeconds === next.durationSeconds &&
    prev.byteSize === next.byteSize &&
    prev.isCollapsed === next.isCollapsed &&
    prev.onToggleCollapse === next.onToggleCollapse &&
    prev.onDock === next.onDock &&
    prev.onDelete === next.onDelete &&
    prev.isBranchFocusActive === next.isBranchFocusActive &&
    prev.onToggleBranchFocus === next.onToggleBranchFocus
  );
}

function documentNodePropsAreEqual(
  prev: Readonly<NodeProps<DocumentFlowNode>>,
  next: Readonly<NodeProps<DocumentFlowNode>>,
): boolean {
  return prev.selected === next.selected && documentNodeDataAreEqual(prev.data, next.data);
}

export const DocumentNodeView = memo(DocumentNodeViewImpl, documentNodePropsAreEqual);
