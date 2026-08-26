import type { Node, NodeProps } from "@xyflow/react";
import { memo, useState } from "react";
import { withAuthToken } from "../../lib/auth/token";
import { downloadBlob } from "./downloadTextFile";
import type { MenuPosition } from "./menuPosition";
import { NodeMenu } from "./NodeMenu";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";

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
 * revoked immediately after). "Regenerate Image" is likewise no longer
 * deferred as of R4.4a: it now calls the real regenerateImage intent -
 * unlike CodeNodeView's own onRegenerate, no client-side parent-lookup/
 * null-guard is needed here, since the backend resolves the ImageNode's
 * parent chat node internally (see sceneStore.ts's regenerateImage). Hide
 * Other Branches is likewise no longer deferred as of R8a: it now calls the
 * real onToggleBranchFocus intent, closed over this node's own id by
 * SceneCanvas, and its label flips to "Show All Branches" once
 * isBranchFocusActive is true - both fields (and the dimming itself) are
 * SceneCanvas's concern, this file just renders whatever it's handed.
 */

export interface ImageNodeData extends Record<string, unknown> {
  imageAssetId: string;
  prompt: string;
  onDelete: () => void;
  onRegenerate: () => void;
  isBranchFocusActive: boolean;
  onToggleBranchFocus: () => void;
}

export type ImageFlowNode = Node<ImageNodeData, "image">;

/** The one place this file turns an asset id into a URL - the <img> render
 * below and both menu actions (Copy Image, Export Image) all call this, so
 * they can never disagree with each other about the endpoint shape. */
function assetUrl(imageAssetId: string): string {
  // ADR-004 stage 4.1: carries the capability token as a query param, since
  // the <img> render below is loaded by the browser's own image loader and
  // cannot be given an Authorization header. A no-op when no token is
  // present (vitest, vite-dev).
  return withAuthToken(`/api/assets/${imageAssetId}`);
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

/** Fetches the asset and saves it as a file via downloadTextFile.ts's shared
 * downloadBlob. */
async function handleExportImage(imageAssetId: string, filename: string): Promise<void> {
  try {
    const response = await fetch(assetUrl(imageAssetId));
    const blob = await response.blob();
    downloadBlob(blob, filename);
  } catch (error) {
    console.error("[image-node] Export Image failed:", error);
  }
}

function ImageNodeMenu({
  position,
  imageAssetId,
  filename,
  prompt,
  onDelete,
  onRegenerate,
  isBranchFocusActive,
  onToggleBranchFocus,
  onClose,
}: {
  position: MenuPosition;
  imageAssetId: string;
  filename: string;
  prompt: string;
  onDelete: () => void;
  onRegenerate: () => void;
  isBranchFocusActive: boolean;
  onToggleBranchFocus: () => void;
  onClose: () => void;
}) {


  return (
    <NodeMenu position={position} onClose={onClose} className="chat-node-menu">
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
      {prompt.trim() && (
        <button
          type="button"
          role="menuitem"
          onClick={() => {
            onRegenerate();
            onClose();
          }}
        >
          Regenerate Image
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
        Delete Image
      </button>
    </NodeMenu>
  );
}

function ImageNodeViewImpl({ id, data, selected }: NodeProps<ImageFlowNode>) {
  const collapsed = useLodVisibility();
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  const [imageFailed, setImageFailed] = useState(false);

  const altText = data.prompt || "Generated image";

  return (
    <NodeShell
      kindClassName="image-node"
      selected={!!selected}
      collapsed={collapsed}
      onContextMenu={(event) => {
        event.preventDefault();
        setMenuPosition({ x: event.clientX, y: event.clientY });
      }}
      header={
        <div className="scene-node-title image-node-title">
          <span>{data.prompt || "Image"}</span>
        </div>
      }
      bodyClassName="image-node-content"
      menu={
        menuPosition && (
          <ImageNodeMenu
            position={menuPosition}
            imageAssetId={data.imageAssetId}
            filename={buildDownloadFilename(id, data.prompt)}
            prompt={data.prompt}
            onDelete={data.onDelete}
            onRegenerate={data.onRegenerate}
            isBranchFocusActive={data.isBranchFocusActive}
            onToggleBranchFocus={data.onToggleBranchFocus}
            onClose={() => setMenuPosition(null)}
          />
        )
      }
    >
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
    </NodeShell>
  );
}

/** ADR-011 stage 11.1: every prop this view actually reads, compared - `id`
 * and `selected` directly, then every field of `data` (all primitives or
 * stable callback references here, so `===` is correct for each - no nested
 * object/array fields on ImageNodeData that would need a deeper compare). Too
 * loose here (e.g. skipping a field) would mean an edit to that field
 * silently fails to re-render this node; too tight (e.g. comparing `data` by
 * reference) would defeat memoization entirely, since toFlowNodes mints a
 * fresh `data` object on every snapshot. */
function imageNodePropsAreEqual(
  prev: Readonly<NodeProps<ImageFlowNode>>,
  next: Readonly<NodeProps<ImageFlowNode>>,
): boolean {
  return (
    prev.id === next.id &&
    prev.selected === next.selected &&
    prev.data.imageAssetId === next.data.imageAssetId &&
    prev.data.prompt === next.data.prompt &&
    prev.data.onDelete === next.data.onDelete &&
    prev.data.onRegenerate === next.data.onRegenerate &&
    prev.data.isBranchFocusActive === next.data.isBranchFocusActive &&
    prev.data.onToggleBranchFocus === next.data.onToggleBranchFocus
  );
}

export const ImageNodeView = memo(ImageNodeViewImpl, imageNodePropsAreEqual);
