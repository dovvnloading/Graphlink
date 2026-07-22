import { Handle, Position, useStore, type Node, type NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import { LOD_ZOOM_THRESHOLD } from "./canvasConstants";

/**
 * The image node (Qt-removal plan R3.21/R3.22) - graphlink_node_image.py's
 * React successor: a card holding a single generated/attached image,
 * push-only same as chat/code/document/thinking/html (content arrives via
 * the scene document; the WS-side addImageNode intent has no live UI
 * trigger yet - same posture addCodeNode/addDocumentNode/addThinkingNode/
 * addHtmlNode were in when they first shipped - see sceneStore.ts). Unlike
 * ChatNode/DocumentNode/HtmlNode, ImageNode has no manual collapse toggle at
 * all - like CodeNode/ThinkingNode, it only ever auto-collapses on zoom
 * (LOD), since the legacy ImageNode has no collapse concept whatsoever
 * (confirmed during R3.21/R3.22 design).
 *
 * The image bytes themselves NEVER ride the scene WS topic - only a small
 * imageAssetId reference string does (backend/canvas.py's SceneNodeRow gains
 * this field for every kind, empty string for non-image rows). The actual
 * bytes are fetched over a plain HTTP GET to /api/assets/{assetId}, a normal
 * browser-fetchable URL (not a WS topic, unlike every other node's content) -
 * both the <img src> below and the Copy/Export menu actions hit that same
 * endpoint via assetUrl() so all three call sites can never drift from one
 * another.
 *
 * Real: render (via <img src>, with an onError-driven "Image unavailable"
 * placeholder - the legacy app's own verbatim text - for a broken/unknown
 * asset id), delete (generic cascade-delete; image nodes are never branch
 * points, so there's no reparent rule to honor), Copy Image (fetch -> blob
 * -> navigator.clipboard.write with a real ClipboardItem - NOT a data URI or
 * an <img>, Clipboard API image writes require an actual Blob), Export Image
 * (fetch -> blob -> object URL -> a temporary anchor's programmatic download,
 * revoked immediately after). Deferred, with an honest disabled+title label
 * rather than a silent drop (same posture every sibling node menu takes):
 * Hide Other Branches (branch visibility isn't built yet - unscoped, not
 * owned by any R-phase) and Regenerate Image (R4's agent layer - same
 * agent-layer-blocked semantics as Chat's "Regenerate Response").
 */

export interface ImageNodeData extends Record<string, unknown> {
  imageAssetId: string;
  prompt: string;
  onDelete: () => void;
}

export type ImageFlowNode = Node<ImageNodeData, "image">;

interface MenuPosition {
  x: number;
  y: number;
}

/** The one place this file turns an asset id into a URL - the <img> render
 * below and both menu actions (Copy Image, Export Image) all call this, so
 * they can never disagree with each other about the endpoint shape. */
function assetUrl(imageAssetId: string): string {
  return `/api/assets/${imageAssetId}`;
}

/** A reasonable download filename for Export Image: the prompt, slugified,
 * falling back to the node id when there's no prompt to work with. Kept
 * deliberately simple - the asset endpoint's Content-Type is the real source
 * of truth for what the bytes are, and browsers don't require a "correct"
 * extension to accept a download. */
function buildDownloadFilename(nodeId: string, prompt: string): string {
  const base = prompt.trim() || nodeId;
  const slug = base
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-+|-+$)/g, "");
  return `${slug || nodeId}.png`;
}

/** Fetch the asset, then hand the browser's Clipboard API a real Blob via
 * ClipboardItem (not a data URI, not an <img> element - navigator.clipboard.
 * write's image path requires an actual Blob). Any failure here (missing
 * Clipboard API, a permissions prompt the user denied, an insecure context)
 * is swallowed rather than thrown - a best-effort menu action should never
 * crash the node, it should just silently not have copied anything. */
async function handleCopyImage(imageAssetId: string): Promise<void> {
  try {
    const response = await fetch(assetUrl(imageAssetId));
    const blob = await response.blob();
    await navigator.clipboard.write([new ClipboardItem({ [blob.type]: blob })]);
  } catch (error) {
    console.error("[image-node] Copy Image failed:", error);
  }
}

/** Fetch the asset, wrap it in an object URL, and drive a temporary,
 * never-attached-to-view anchor's download through a programmatic click -
 * the standard "save this blob as a file" browser pattern. The object URL is
 * revoked immediately after the click to avoid leaking it (the click itself
 * is synchronous, so the browser has already captured what it needs from the
 * URL by the time revokeObjectURL runs on the next line). */
async function handleExportImage(imageAssetId: string, filename: string): Promise<void> {
  try {
    const response = await fetch(assetUrl(imageAssetId));
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(objectUrl);
  } catch (error) {
    console.error("[image-node] Export Image failed:", error);
  }
}

function ImageNodeMenu({
  position,
  imageAssetId,
  filename,
  onDelete,
  onClose,
}: {
  position: MenuPosition;
  imageAssetId: string;
  filename: string;
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
      {/* Legacy order: Copy Image, Export Image, separator, Hide Other
          Branches, Regenerate Image, Delete Image. */}
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          void handleCopyImage(imageAssetId);
          onClose();
        }}
      >
        Copy Image
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          void handleExportImage(imageAssetId, filename);
          onClose();
        }}
      >
        Export Image
      </button>
      <div className="chat-node-menu-separator" role="separator" />
      <button type="button" role="menuitem" disabled title="Branch visibility isn't built yet">
        Hide Other Branches
      </button>
      <button type="button" role="menuitem" disabled title="Agent regeneration lands in R4">
        Regenerate Image
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
        Delete Image
      </button>
    </div>
  );
}

export function ImageNodeView({ id, data, selected }: NodeProps<ImageFlowNode>) {
  const zoom = useStore((s) => s.transform[2]);
  const collapsed = zoom < LOD_ZOOM_THRESHOLD;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  const [imageFailed, setImageFailed] = useState(false);

  const altText = data.prompt || "Generated image";

  return (
    <div
      className={`scene-node image-node${selected ? " selected" : ""}${collapsed ? " collapsed" : ""}`}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
    >
      <Handle type="target" position={Position.Top} className="scene-node-handle" />
      <div className="scene-node-title image-node-title">
        <span>{data.prompt || "Image"}</span>
      </div>
      {!collapsed && (
        <div className="scene-node-body image-node-content">
          {imageFailed ? (
            <div className="image-node-placeholder">Image unavailable</div>
          ) : (
            <img
              className="image-node-img"
              src={assetUrl(data.imageAssetId)}
              alt={altText}
              onError={() => setImageFailed(true)}
            />
          )}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="scene-node-handle" />
      {menuPosition && (
        <ImageNodeMenu
          position={menuPosition}
          imageAssetId={data.imageAssetId}
          filename={buildDownloadFilename(id, data.prompt)}
          onDelete={data.onDelete}
          onClose={() => setMenuPosition(null)}
        />
      )}
    </div>
  );
}
