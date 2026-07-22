import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";

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
 * branch. Deferred, with an honest disabled+title label rather than a
 * silently-dropped action (see the R3.7-era audit this increment is
 * following up on): Open File (needs a new backend endpoint; browsers
 * cannot open arbitrary local paths), Export (R6, document-kind only -
 * matches the legacy menu's own conditional), Hide Other Branches (branch
 * visibility isn't built yet).
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
}

export type DocumentFlowNode = Node<DocumentNodeData, "document">;

interface MenuPosition {
  x: number;
  y: number;
}

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

  const isAudio = attachmentKind === "audio";
  // Legacy gate is `attachment_kind == "document"` (a strict allow-list), not
  // "not audio" - the two diverge for any other/malformed kind value (e.g. a
  // corrupted saved session). Keep the same strict check here rather than
  // `!isAudio`, which would show the Export placeholder for a kind the
  // legacy menu would never have added it for.
  const isDocumentKind = attachmentKind === "document";

  return (
    <div
      ref={menuRef}
      className="chat-node-menu"
      style={{ position: "fixed", left: position.x, top: position.y }}
      role="menu"
    >
      {/* Order verified against graphlink_node_document_menu.py's own
          construction order: Copy Details, Collapse/Expand, Dock, Open File
          (conditional), separator, Export (conditional), Hide Other
          Branches, Delete. */}
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          navigator.clipboard.writeText(content);
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
        <button type="button" role="menuitem" disabled title="Export lands in R6">
          Export
        </button>
      )}
      <button type="button" role="menuitem" disabled title="Branch visibility isn't built yet">
        Hide Other Branches
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
    </div>
  );
}

// -- view ----------------------------------------------------------------

export function DocumentNodeView({ data, selected }: NodeProps<DocumentFlowNode>) {
  const zoom = useStore((s) => s.transform[2]);
  const lodCollapsed = zoom < LOD_ZOOM_THRESHOLD;
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
    <div
      className={`scene-node document-node${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
    >
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
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
      {!collapsed && (
        <div className="scene-node-body document-node-content">
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
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menuPosition && (
        <DocumentNodeMenu
          position={menuPosition}
          content={data.content}
          attachmentKind={data.attachmentKind}
          filePath={data.filePath}
          isCollapsed={data.isCollapsed}
          onToggleCollapse={data.onToggleCollapse}
          onDock={data.onDock}
          onDelete={data.onDelete}
          onClose={() => setMenuPosition(null)}
        />
      )}
    </div>
  );
}
