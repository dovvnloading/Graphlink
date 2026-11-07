import { useReactFlow } from "@xyflow/react";
import { useMemo, useState } from "react";
import type { OrganizationOptions, OrganizationPreview, SceneStore } from "../canvas/sceneStore";
import { Dialog, useOverlays } from "../overlays/overlays";
import "./OrganizationDialog.css";

type Scope = OrganizationOptions["scope"];
type PreviewState = { data: OrganizationPreview; options: OrganizationOptions };
type PreviewNode = OrganizationPreview["nodes"][number];

const VIEW_WIDTH = 520;
const VIEW_HEIGHT = 245;
const INSET = 12;

/** Independent of the live graph: this miniature never moves real nodes. */
export function previewNodeRects(nodes: PreviewNode[]) {
  const visible = nodes.filter(
    (n) => Number.isFinite(n.x) && Number.isFinite(n.y)
      && Number.isFinite(n.width) && Number.isFinite(n.height)
      && n.width > 0 && n.height > 0,
  );
  if (!visible.length) return [];
  const left = Math.min(...visible.map((n) => n.x));
  const top = Math.min(...visible.map((n) => n.y));
  const right = Math.max(...visible.map((n) => n.x + n.width));
  const bottom = Math.max(...visible.map((n) => n.y + n.height));
  const scale = Math.min(
    (VIEW_WIDTH - INSET * 2) / Math.max(1, right - left),
    (VIEW_HEIGHT - INSET * 2) / Math.max(1, bottom - top),
  );
  const xInset = (VIEW_WIDTH - (right - left) * scale) / 2;
  const yInset = (VIEW_HEIGHT - (bottom - top) * scale) / 2;
  return visible.map((n) => ({
    ...n,
    px: (n.x - left) * scale + xInset,
    py: (n.y - top) * scale + yInset,
    pw: n.width * scale,
    ph: n.height * scale,
  }));
}

function OrganizationMiniature({ plan }: { plan: OrganizationPreview }) {
  const rectangles = useMemo(() => previewNodeRects(plan.nodes), [plan.nodes]);
  const groupRects = rectangles.filter((n) => n.kind === "frame" || n.kind === "container");
  const contentRects = rectangles.filter((n) => n.kind !== "frame" && n.kind !== "container");
  return (
    <div className="organization-miniature">
      <svg
        viewBox={"0 0 " + VIEW_WIDTH + " " + VIEW_HEIGHT}
        role="img"
        aria-label={"Proposed arrangement of " + plan.nodes.length + " nodes; " + plan.movedCount + " change"}
      >
        {groupRects.map((node) => (
          <rect
            key={node.id}
            x={node.px} y={node.py} width={node.pw} height={node.ph}
            rx={3} className="organization-miniature-group"
          >
            <title>{node.title || node.kind}</title>
          </rect>
        ))}
        {contentRects.map((node) => (
          <rect
            key={node.id}
            x={node.px} y={node.py} width={Math.max(1, node.pw)} height={Math.max(1, node.ph)}
            rx={3}
            className={node.changed ? "organization-miniature-node moved" : "organization-miniature-node"}
          >
            <title>{node.title || node.kind}</title>
          </rect>
        ))}
      </svg>
    </div>
  );
}

export function OrganizationDialog({ store }: { store: SceneStore }) {
  const overlays = useOverlays();
  const rf = useReactFlow();
  const isOpen = overlays.isOpen("organize");
  const [wasOpen, setWasOpen] = useState(isOpen);
  const [scope, setScope] = useState<Scope>("all");
  const [preserveFrameSizes, setPreserveFrameSizes] = useState(true);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // Reset on reopening without a setState-in-effect flash of stale plans.
  if (isOpen !== wasOpen) {
    setWasOpen(isOpen);
    if (isOpen) {
      setPreview(null);
      setError("");
      setBusy(false);
    }
  }

  if (!isOpen) return null;

  function optionsFromSelection(): OrganizationOptions {
    const selected = rf.getNodes().filter((node) => node.selected).map((node) => node.id);
    let rootId: string | null = null;
    if (scope === "selection" && selected.length === 0) {
      throw new Error("Select at least one node on the canvas first.");
    }
    if (scope === "branch" || scope === "group") {
      if (selected.length !== 1) {
        throw new Error("Select exactly one branch root or group on the canvas first.");
      }
      rootId = selected[0];
      const kind = store.getScene().nodes.find((node) => node.id === rootId)?.kind;
      if (scope === "group" && kind !== "frame" && kind !== "container") {
        throw new Error("For group scope, select a frame or container.");
      }
      if (scope === "branch" && (kind === "frame" || kind === "container")) {
        throw new Error("For branch scope, select a content node.");
      }
    }
    return {
      scope, rootId,
      nodeIds: scope === "selection" ? selected : null,
      fixedNodeIds: null,
      preserveFrameSizes,
    };
  }

  async function onPreview() {
    if (busy) return;
    setBusy(true);
    setError("");
    setPreview(null);
    try {
      const options = optionsFromSelection();
      const data = await store.previewOrganize(options);
      setPreview({ data, options });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function onApply() {
    if (busy || !preview) return;
    setBusy(true);
    setError("");
    try {
      await store.applyOrganize(preview.options, preview.data.planId);
      overlays.close();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      // Never silently apply a stale preview. Require an explicit re-preview.
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog name="organize" title="Organize graph" className="organization-dialog">
      <p className="organization-description">
        Preview an arrangement before moving anything. Connections and group membership are preserved.
      </p>
      <div className="organization-options">
        <label htmlFor="organization-scope">Organize</label>
        <select
          id="organization-scope" value={scope}
          onChange={(event) => { setScope(event.target.value as Scope); setPreview(null); setError(""); }}
          disabled={busy}
        >
          <option value="all">Entire canvas</option>
          <option value="selection">Selected nodes</option>
          <option value="branch">Selected branch and descendants</option>
          <option value="group">Selected frame/container contents</option>
        </select>
        <label className="organization-check">
          <input
            type="checkbox" checked={preserveFrameSizes} disabled={busy}
            onChange={(event) => { setPreserveFrameSizes(event.target.checked); setPreview(null); setError(""); }}
          />
          Preserve manually resized frame dimensions
        </label>
      </div>
      {preview && (
        <div className="organization-result">
          <div className="organization-summary">
            <strong>{preview.data.movedCount} nodes or group bounds change</strong>
            <span>{preview.data.selectedCount} content nodes in scope</span>
          </div>
          <OrganizationMiniature plan={preview.data} />
          <p className="organization-hint">The preview is read-only. Applying makes one undoable change.</p>
        </div>
      )}
      {error && <p className="organization-error" role="alert">{error}</p>}
      <div className="organization-actions">
        <button type="button" onClick={() => void onPreview()} disabled={busy}>
          {busy && !preview ? "Previewing…" : preview ? "Refresh preview" : "Preview layout"}
        </button>
        <button type="button" onClick={() => void onApply()} disabled={!preview || busy} className="organization-apply">
          {busy && preview ? "Applying…" : "Apply layout"}
        </button>
      </div>
    </Dialog>
  );
}
