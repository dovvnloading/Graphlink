import { BaseEdge, type EdgeProps } from "@xyflow/react";

/**
 * R7.5b-2: the second canvas-visual parity fix (Qt-removal plan R7.5) - a
 * right-angle "step" connection path, the first custom edge component in
 * this codebase (confirmed by recon: no precedent anywhere in web_ui/src).
 *
 * DELIBERATE TRANSLATION, not a transcription: legacy's default
 * ConnectionItem.update_path() used a horizontal mid-X step (start -> midX,
 * startY -> midX,endY -> end) because Qt's primary connections ran
 * left-to-right. Every node view in this app uses `Handle target=Top /
 * source=Bottom` universally (branches lay out top-to-bottom, not
 * left-to-right), so the geometrically correct translation is the mid-Y
 * variant - the shape legacy called GroupSummaryConnectionItem, applied here
 * as the general case rather than a special one. Sanity-checked against a
 * real running instance before shipping (see the plan doc ledger).
 *
 * `style` is forwarded to BaseEdge so this composes correctly with faded
 * connections (SceneCanvas.tsx's toFlowEdges) when both toggles are on at
 * once - an orthogonal edge must still dim like any other.
 */
export function OrthogonalEdge({ sourceX, sourceY, targetX, targetY, style, markerEnd }: EdgeProps) {
  const midY = sourceY + (targetY - sourceY) / 2;
  const path = `M ${sourceX},${sourceY} L ${sourceX},${midY} L ${targetX},${midY} L ${targetX},${targetY}`;
  return <BaseEdge path={path} style={style} markerEnd={markerEnd} />;
}
