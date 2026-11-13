import { BaseEdge, type EdgeProps } from "@xyflow/react";
import { connectionDashes, type ConnectionRole } from "./connections/connectionAppearance";
import { connectionPath, connectionSvgPath } from "./connections/connectionGeometry";

/** SVG counterpart of the immediate-mode canvas; used during PNG capture. */
function ExportEdge({ orthogonal, ...props }: EdgeProps & { orthogonal: boolean }) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd } = props;
  const path = connectionPath(
    { x: sourceX, y: sourceY }, sourcePosition, { x: targetX, y: targetY }, targetPosition, orthogonal,
  );
  return (
    <BaseEdge
      path={connectionSvgPath(path)}
      markerEnd={markerEnd}
      interactionWidth={0}
      style={{
        // html-to-image deep-clones SVG subtrees without inlining classes.
        fill: "none",
        stroke: "var(--gl-connection-line)",
        strokeWidth: 1.5,
        strokeDasharray: connectionDashes(data?.role as ConnectionRole, 1).join(" ") || undefined,
        strokeLinecap: "round",
        strokeLinejoin: "round",
        vectorEffect: "non-scaling-stroke",
        ...props.style,
      }}
    />
  );
}

export function OrthogonalEdge(props: EdgeProps) {
  return <ExportEdge {...props} orthogonal />;
}

export function CurvedEdge(props: EdgeProps) {
  return <ExportEdge {...props} orthogonal={false} />;
}
