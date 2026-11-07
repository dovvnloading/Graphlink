import { useReactFlow } from "@xyflow/react";
import { useMemo, useState } from "react";
import type { OrganizationOptions, OrganizationPreview, SceneStore } from "../canvas/sceneStore";
import { Dialog, useOverlays } from "../overlays/overlays";
import "./OrganizationDialog.css";

type Scope = OrganizationOptions["scope"];
type PreviewState = { data: OrganizationPreview; options: OrganizationOptions };
type PreviewNode = OrganizationPreview["nodes"][number];
type PreviewRect = Pick<PreviewNode, "id" | "kind" | "title" | "x" | "y" | "width" | "height" | "changed">;
type PreviewStage = "before" | "after";

const VIEW_WIDTH = 520;
const VIEW_HEIGHT = 245;
const INSET = 12;

function isValidRect(node: PreviewRect) {
  return Number.isFinite(node.x) && Number.isFinite(node.y)
    && Number.isFinite(node.width) && Number.isFinite(node.height)
    && node.width > 0 && node.height > 0;
}

/** Fit to a common coordinate system so before/after preserve the mental map. */
function fitNodeRects(nodes: PreviewRect[], allRects: PreviewRect[]) {
  const bounds = allRects.filter(isValidRect);
  if (!bounds.length) return [];
  const left = Math.min(...bounds.map((n) => n.x));
  const top = Math.min(...bounds.map((n) => n.y));
  const right = Math.max(...bounds.map((n) => n.x + n.width));
  const bottom = Math.max(...bounds.map((n) => n.y + n.height));
  const scale = Math.min(
    (VIEW_WIDTH - INSET * 2) / Math.max(1, right - left),
    (VIEW_HEIGHT - INSET * 2) / Math.max(1, bottom - top),
  );
  const xInset = (VIEW_WIDTH - (right - left) * scale) / 2;
  const yInset = (VIEW_HEIGHT - (bottom - top) * scale) / 2;
  return nodes.filter(isValidRect).map((n) => ({
    ...n,
    px: (n.x - left) * scale + xInset,
    py: (n.y - top) * scale + yInset,
    pw: n.width * scale,
    ph: n.height * scale,
  }));
}

/** Independent of the live graph: this miniature never moves real nodes. */
export function previewNodeRects(nodes: PreviewRect[]) {
  return fitNodeRects(nodes, nodes);
}

export function previewComparisonRects(nodes: PreviewNode[], stage: PreviewStage) {
  const before = nodes.map((n) => ({
    ...n, x: n.previousX, y: n.previousY,
    width: n.previousWidth, height: n.previousHeight,
  }));
  return fitNodeRects(stage === "before" ? before : nodes, [...before, ...nodes]);
}

type DrawnNode = ReturnType<typeof previewNodeRects>[number];

/** A port-based orthogonal *preview*, not a collision-certified edge route. */
export function previewEdgePath(source: DrawnNode, target: DrawnNode): string {
  const sx = source.px + source.pw / 2;
  const sy = source.py + source.ph / 2;
  const tx = target.px + target.pw / 2;
  const ty = target.py + target.ph / 2;
  if (Math.abs(tx - sx) > Math.abs(ty - sy)) {
    const startX = tx >= sx ? source.px + source.pw : source.px;
    const endX = tx >= sx ? target.px : target.px + target.pw;
    const middle = (startX + endX) / 2;
    return "M " + startX + " " + sy + " L " + middle + " " + sy
      + " L " + middle + " " + ty + " L " + endX + " " + ty;
  }
  const startY = ty >= sy ? source.py + source.ph : source.py;
  const endY = ty >= sy ? target.py : target.py + target.ph;
  const middle = (startY + endY) / 2;
  return "M " + sx + " " + startY + " L " + sx + " " + middle
    + " L " + tx + " " + middle + " L " + tx + " " + endY;
}

function OrganizationMiniature({
  plan, stage, showConnections,
}: {
  plan: OrganizationPreview;
  stage: PreviewStage;
  showConnections: boolean;
}) {
  const rectangles = useMemo(() => previewComparisonRects(plan.nodes, stage), [plan.nodes, stage]);
  const groups = rectangles.filter((n) => n.kind === "frame" || n.kind === "container");
  const cards = rectangles.filter((n) => n.kind !== "frame" && n.kind !== "container");
  const paths = useMemo(() => {
    if (!showConnections) return [];
    const byId = new Map(rectangles.map((rect) => [rect.id, rect]));
    return plan.edges.flatMap((edge) => {
      const from = byId.get(edge.source);
      const to = byId.get(edge.target);
      return from && to && from.id !== to.id
        ? [{ id: edge.id, d: previewEdgePath(from, to) }]
        : [];
    });
  }, [plan.edges, rectangles, showConnections]);
  const label = stage === "before" ? "Original arrangement" : "Proposed arrangement";
  return (
    <div className="organization-miniature">
      <svg
        viewBox={"0 0 " + VIEW_WIDTH + " " + VIEW_HEIGHT}
        role="img"
        aria-label={label + " of " + plan.nodes.length + " visible nodes, with " + paths.length + " connections"}
      >
        {groups.map((node) => (
          <rect
            key={node.id}
            x={node.px} y={node.py} width={node.pw} height={node.ph}
            rx={3} className="organization-miniature-group"
          >
            <title>{node.title || node.kind}</title>
          </rect>
        ))}
        {paths.map((edge) => (
          <path key={edge.id} d={edge.d} className="organization-miniature-edge" />
        ))}
        {cards.map((node) => (
          <rect
            key={node.id}
            x={node.px} y={node.py} width={Math.max(1, node.pw)} height={Math.max(1, node.ph)}
            rx={3}
            className={stage === "after" && node.changed
              ? "organization-miniature-node moved"
              : "organization-miniature-node"}
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
  const [pinSelection, setPinSelection] = useState(false);
  const [showConnections, setShowConnections] = useState(true);
  const [viewMode, setViewMode] = useState<"compare" | "after">("compare");
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
      fixedNodeIds: pinSelection ? selected : null,
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
        <label className="organization-check">
          <input
            type="checkbox" checked={pinSelection} disabled={busy}
            onChange={(event) => { setPinSelection(event.target.checked); setPreview(null); setError(""); }}
          />
          Keep currently selected nodes fixed (hard constraint)
        </label>
      </div>
      {preview && (
        <div className="organization-result">
          <div className="organization-summary">
            <strong>{preview.data.movedCount} nodes or group bounds change</strong>
            <span>{preview.data.selectedCount} content nodes in scope</span>
            <span>
              Diagram area: {preview.data.areaBefore.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              {" → "}
              {preview.data.areaAfter.toLocaleString(undefined, { maximumFractionDigits: 0 })} px²
            </span>
          </div>
          <div className="organization-preview-controls">
            <div className="organization-view-toggle" role="group" aria-label="Preview layout view">
              <button type="button" aria-pressed={viewMode === "compare"} onClick={() => setViewMode("compare")}>
                Before / after
              </button>
              <button type="button" aria-pressed={viewMode === "after"} onClick={() => setViewMode("after")}>
                After only
              </button>
            </div>
            <label className="organization-check">
              <input
                type="checkbox" checked={showConnections}
                onChange={(event) => setShowConnections(event.target.checked)}
              />
              Show connections ({preview.data.edges.length})
            </label>
          </div>
          <div className={viewMode === "compare" ? "organization-comparison" : "organization-comparison after-only"}>
            {viewMode === "compare" && (
              <div className="organization-pane">
                <h3>Before</h3>
                <OrganizationMiniature plan={preview.data} stage="before" showConnections={showConnections} />
              </div>
            )}
            <div className="organization-pane">
              <h3>After</h3>
              <OrganizationMiniature plan={preview.data} stage="after" showConnections={showConnections} />
            </div>
          </div>
          <p className="organization-hint">
            Selected cards can be pinned in place. The preview does not change your canvas;
            Apply revalidates the plan server-side and creates one undoable change.
          </p>
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
