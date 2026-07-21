import { useEffect, useRef, useState } from "react";
import { GridControlState, initialGridControlState } from "./bridgeTypes";
import { BridgeRejection, GridControlBridge, createGridControlBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

function App() {
  const [state, setState] = useState<GridControlState>(initialGridControlState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  // The 4 routing/alignment checkboxes are pure client-side state, matching
  // the toolbar's own `controlsChecked` precedent - nothing else in the app
  // reads this back (it writes straight onto ChatScene), so there is no
  // server round-trip to keep in sync. Persists across show/hide toggles
  // the same way the legacy widget's own living QCheckBox instances did,
  // since this island is only ever hidden/shown, never unmounted.
  const [snapToGrid, setSnapToGrid] = useState(false);
  const [orthogonalConnections, setOrthogonalConnections] = useState(false);
  const [smartGuides, setSmartGuides] = useState(false);
  const [fadeConnections, setFadeConnections] = useState(false);
  const bridgeRef = useRef<GridControlBridge | null>(null);
  const shellRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const bridge = createGridControlBridge(setState, setRejection);
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

  if (rejection) {
    return (
      <BridgeErrorState
        title="The grid control panel is unavailable"
        rejection={rejection}
        className="grid-control-shell bridge-error"
      />
    );
  }

  return (
    <div className="grid-control-shell" ref={shellRef}>
      <p className="grid-control-title">Grid</p>

      <input
        type="range"
        className="grid-control-slider"
        min={0}
        max={100}
        value={state.gridOpacityPercent}
        aria-label="Grid opacity"
        onChange={(event) => bridgeRef.current?.setGridOpacityPercent(Number(event.target.value))}
      />

      <div className="grid-control-row">
        {state.sizePresets.map((size) => (
          <button
            key={size}
            type="button"
            className={"grid-control-preset-btn" + (size === state.gridSize ? " active" : "")}
            onClick={() => bridgeRef.current?.setGridSize(size)}
          >
            {size}px
          </button>
        ))}
      </div>

      <div className="grid-control-row">
        {state.stylePresets.map((style) => (
          <button
            key={style}
            type="button"
            className={"grid-control-preset-btn" + (style === state.gridStyle ? " active" : "")}
            onClick={() => bridgeRef.current?.setGridStyle(style)}
          >
            {style}
          </button>
        ))}
      </div>

      <div className="grid-control-row">
        {state.colorPresets.map((color) => (
          <button
            key={color}
            type="button"
            className={"grid-control-color-swatch" + (color === state.gridColor ? " active" : "")}
            style={{ backgroundColor: color }}
            aria-label={`Grid color ${color}`}
            onClick={() => bridgeRef.current?.setGridColor(color)}
          />
        ))}
      </div>

      <p className="grid-control-section-label">Alignment &amp; Routing</p>
      <label className="grid-control-checkbox-row">
        <input
          type="checkbox"
          checked={snapToGrid}
          onChange={(event) => {
            setSnapToGrid(event.target.checked);
            bridgeRef.current?.setSnapToGrid(event.target.checked);
          }}
        />
        Snap to Grid
      </label>
      <label className="grid-control-checkbox-row">
        <input
          type="checkbox"
          checked={orthogonalConnections}
          onChange={(event) => {
            setOrthogonalConnections(event.target.checked);
            bridgeRef.current?.setOrthogonalConnections(event.target.checked);
          }}
        />
        Orthogonal Connections
      </label>
      <label className="grid-control-checkbox-row">
        <input
          type="checkbox"
          checked={smartGuides}
          onChange={(event) => {
            setSmartGuides(event.target.checked);
            bridgeRef.current?.setSmartGuides(event.target.checked);
          }}
        />
        Smart Guides
      </label>

      <p className="grid-control-section-label">Connection Rendering</p>
      <label className="grid-control-checkbox-row" title="Keep connections quiet until you hover them.">
        <input
          type="checkbox"
          checked={fadeConnections}
          onChange={(event) => {
            setFadeConnections(event.target.checked);
            bridgeRef.current?.setFadeConnections(event.target.checked);
          }}
        />
        Faded Connections
      </label>
    </div>
  );
}

export default App;
