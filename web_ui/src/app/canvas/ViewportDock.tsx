import { Panel, useReactFlow, useStore } from "@xyflow/react";
import { IconButton } from "../ui/IconButton";
import { Tooltip } from "../ui/Tooltip";
import { motionDuration } from "../reducedMotion";
import { GRAPH_FIT_OPTIONS } from "./canvasConstants";
import "./ViewportDock.css";

export function ViewportDock({ hasNodes }: { hasNodes: boolean }) {
  const flow = useReactFlow();
  const percentage = useStore((state) => Math.round(state.transform[2] * 100));
  const atMinimum = useStore((state) => state.transform[2] <= state.minZoom);
  const atMaximum = useStore((state) => state.transform[2] >= state.maxZoom);

  return (
    <Panel position="bottom-left" className="viewport-dock" role="group" aria-label="Canvas navigation">
      <IconButton icon="minus" label="Zoom Out" shortcut="-" size="md"
        disabled={atMinimum} onClick={() => flow.zoomOut({ duration: motionDuration(150) })} />
      <Tooltip label="Reset zoom to 100%" shortcut="0">
        <button type="button" className="viewport-scale" aria-label="Reset zoom to 100%"
          aria-keyshortcuts="0"
          onClick={() => flow.zoomTo(1, { duration: motionDuration(200) })}>
          {percentage}%
        </button>
      </Tooltip>
      <IconButton icon="plus" label="Zoom In" shortcut="+" size="md"
        disabled={atMaximum} onClick={() => flow.zoomIn({ duration: motionDuration(150) })} />
      <span className="viewport-divider" aria-hidden="true" />
      <IconButton icon="fit-view" label="Fit All" shortcut="Shift+1" size="md"
        disabled={!hasNodes}
        onClick={() => flow.fitView({ ...GRAPH_FIT_OPTIONS, duration: motionDuration(200) })} />
    </Panel>
  );
}
