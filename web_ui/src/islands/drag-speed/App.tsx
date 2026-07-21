import { useEffect, useRef, useState } from "react";
import { DragSpeedState, initialDragSpeedState } from "./bridgeTypes";
import { BridgeRejection, DragSpeedBridge, createDragSpeedBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

const DEFAULT_PERCENT = 100;

function App() {
  const [state, setState] = useState<DragSpeedState>(initialDragSpeedState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  // Fire-and-forget, matching the legacy control_widget exactly - it never
  // read a live drag factor back either (its slider always started at a
  // hardcoded 100%).
  const [percent, setPercent] = useState(DEFAULT_PERCENT);
  const bridgeRef = useRef<DragSpeedBridge | null>(null);
  const shellRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const bridge = createDragSpeedBridge(setState, setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  useEffect(() => {
    const element = shellRef.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const height = entries[0]?.contentRect.height;
      if (height) bridgeRef.current?.resize(Math.ceil(height));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  function applyPercent(value: number) {
    setPercent(value);
    bridgeRef.current?.setDragFactor(value / 100);
  }

  if (rejection) {
    return (
      <BridgeErrorState
        title="The drag speed panel is unavailable"
        rejection={rejection}
        className="drag-speed-shell bridge-error"
      />
    );
  }

  return (
    <div className="drag-speed-shell" ref={shellRef}>
      <div className="drag-speed-row">
        <span className="drag-speed-label">Drag</span>
        <input
          type="range"
          className="drag-speed-slider"
          min={state.percentMin}
          max={state.percentMax}
          value={percent}
          aria-label="Drag speed"
          onChange={(event) => applyPercent(Number(event.target.value))}
        />
      </div>

      <div className="drag-speed-presets">
        {state.percentPresets.map((preset) => (
          <button
            key={preset}
            type="button"
            className={"drag-speed-preset-btn" + (preset === percent ? " active" : "")}
            onClick={() => applyPercent(preset)}
          >
            {preset}%
          </button>
        ))}
      </div>
    </div>
  );
}

export default App;
