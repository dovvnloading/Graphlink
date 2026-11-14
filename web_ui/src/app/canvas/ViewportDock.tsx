import { Panel, useReactFlow, useStore } from "@xyflow/react";
import { AppBarIcon } from "../chrome/AppBarIcon";
import { motionDuration } from "../reducedMotion";
import { GRAPH_FIT_OPTIONS } from "./canvasConstants";

export function ViewportDock({ hasNodes }: { hasNodes: boolean }) {
  const flow = useReactFlow();
  const percentage = useStore((state) => Math.round(state.transform[2] * 100));
  const atMinimum = useStore((state) => state.transform[2] <= state.minZoom);
  const atMaximum = useStore((state) => state.transform[2] >= state.maxZoom);

  return (
    <Panel position="bottom-left" className="viewport-dock" role="group" aria-label="Canvas navigation">
      <button type="button" title="Zoom Out" aria-label="Zoom Out" disabled={atMinimum}
        onClick={() => flow.zoomOut({ duration: motionDuration(150) })}>
        <svg aria-hidden="true" className="icon" viewBox="0 0 24 24"><path d="M5 12h14" /></svg>
      </button>
      <button type="button" className="viewport-scale" title="Reset zoom to 100%" aria-label="Reset zoom to 100%"
        onClick={() => flow.zoomTo(1, { duration: motionDuration(200) })}>
        {percentage}%
      </button>
      <button type="button" title="Zoom In" aria-label="Zoom In" disabled={atMaximum}
        onClick={() => flow.zoomIn({ duration: motionDuration(150) })}>
        <svg aria-hidden="true" className="icon" viewBox="0 0 24 24"><path d="M5 12h14m-7-7v14" /></svg>
      </button>
      <span className="viewport-divider" aria-hidden="true" />
      <button type="button" title="Fit All" aria-label="Fit All" disabled={!hasNodes}
        onClick={() => flow.fitView({ ...GRAPH_FIT_OPTIONS, duration: motionDuration(200) })}>
        <AppBarIcon name="fit" />
      </button>
    </Panel>
  );
}
