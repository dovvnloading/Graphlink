import { useEffect, useRef, useState } from "react";
import { FontControlState, initialFontControlState } from "./bridgeTypes";
import { BridgeRejection, FontControlBridge, createFontControlBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

const DEFAULT_FONT_SIZE = 10;

function App() {
  const [state, setState] = useState<FontControlState>(initialFontControlState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  // Fire-and-forget controls, matching the legacy FontControl widget exactly
  // - it never read the scene's live font state back either (ChatScene has
  // always been the sole owner of "current" font family/size/color). Local
  // UI state here just tracks what the user has picked in THIS control, the
  // same way the legacy QComboBox/QSlider's own widget-internal value did.
  const [family, setFamily] = useState(state.fontFamilies[0] ?? "Segoe UI");
  const [size, setSize] = useState(DEFAULT_FONT_SIZE);
  const bridgeRef = useRef<FontControlBridge | null>(null);
  const shellRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const bridge = createFontControlBridge(setState, setRejection);
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
        title="The font control panel is unavailable"
        rejection={rejection}
        className="font-control-shell bridge-error"
      />
    );
  }

  return (
    <div className="font-control-shell" ref={shellRef}>
      <p className="font-control-title">Font</p>

      <select
        className="font-control-family-select"
        value={family}
        aria-label="Font family"
        onChange={(event) => {
          setFamily(event.target.value);
          bridgeRef.current?.setFontFamily(event.target.value);
        }}
      >
        {state.fontFamilies.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>

      <input
        type="range"
        className="font-control-slider"
        min={state.sizeMin}
        max={state.sizeMax}
        value={size}
        aria-label="Font size"
        onChange={(event) => {
          const value = Number(event.target.value);
          setSize(value);
          bridgeRef.current?.setFontSize(value);
        }}
      />

      <div className="font-control-row">
        {state.colorPresets.map((color) => (
          <button
            key={color}
            type="button"
            className="font-control-color-swatch"
            style={{ backgroundColor: color }}
            aria-label={`Font color ${color}`}
            onClick={() => bridgeRef.current?.setFontColor(color)}
          />
        ))}
      </div>
    </div>
  );
}

export default App;
