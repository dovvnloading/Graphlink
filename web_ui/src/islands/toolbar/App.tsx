import { useEffect, useRef, useState } from "react";
import { ToolbarState, initialToolbarState } from "./bridgeTypes";
import { BridgeRejection, ToolbarBridge, createToolbarBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

// Named anchor points the 4 flyout-opening buttons report their own screen
// rect for, replacing the native QToolButton reference show_for_anchor()
// used to depend on directly - see graphlink_toolbar_bridge.py's own
// AnchorRect docstring for the full rationale.
const ANCHOR_NAMES = ["pins", "plugins", "settings", "help"] as const;
type AnchorName = (typeof ANCHOR_NAMES)[number];

function App() {
  const [state, setState] = useState<ToolbarState>(initialToolbarState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const [controlsChecked, setControlsChecked] = useState(false);
  const bridgeRef = useRef<ToolbarBridge | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const pinsAnchorRef = useRef<HTMLButtonElement>(null);
  const pluginsAnchorRef = useRef<HTMLButtonElement>(null);
  const settingsAnchorRef = useRef<HTMLButtonElement>(null);
  const helpAnchorRef = useRef<HTMLButtonElement>(null);
  const anchorRefs: Record<AnchorName, React.RefObject<HTMLButtonElement>> = {
    pins: pinsAnchorRef,
    plugins: pluginsAnchorRef,
    settings: settingsAnchorRef,
    help: helpAnchorRef,
  };

  useEffect(() => {
    const bridge = createToolbarBridge(setState, setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  function reportAnchors() {
    for (const name of ANCHOR_NAMES) {
      const element = anchorRefs[name].current;
      if (!element) continue;
      const rect = element.getBoundingClientRect();
      bridgeRef.current?.reportAnchorRect(
        name,
        Math.round(rect.left),
        Math.round(rect.top),
        Math.round(rect.width),
        Math.round(rect.height),
      );
    }
  }

  // Report every anchor button's own screen rect whenever the toolbar's
  // overall layout changes (window resize can reflow where "Plugins" sits,
  // since it sits before an expanding spacer) - not just once at mount.
  // getBoundingClientRect() is already relative to this document's own
  // viewport, exactly the DOM-local coordinates graphlink_toolbar_bridge.py's
  // reportAnchorRect() expects (it composes them with the host widget's own
  // mapToGlobal()).
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const observer = new ResizeObserver(reportAnchors);
    observer.observe(root);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The ResizeObserver's own callback only fires on a genuine layout change,
  // and the QWebChannel handshake (connectQWebChannel, inside the bridge
  // construction effect above) completes asynchronously - a one-shot report
  // fired synchronously at mount can race ahead of that handshake and be
  // silently dropped (the bridge exists but isn't "connected" yet), with
  // nothing ever re-triggering it in a static window that never resizes.
  // Re-reporting on every real revision bump is the correctness fix: it
  // guarantees at least one report happens strictly AFTER the channel is
  // provably live (this state update could only have arrived over it).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reportAnchors, [state.revision]);

  if (rejection) {
    return (
      <BridgeErrorState
        title="The toolbar is unavailable"
        rejection={rejection}
        className="toolbar-shell bridge-error"
      />
    );
  }

  function onToggleControls() {
    const next = !controlsChecked;
    setControlsChecked(next);
    bridgeRef.current?.toggleControls(next);
  }

  return (
    <div className="toolbar-shell" ref={rootRef}>
      <button type="button" className="toolbar-btn" onClick={() => bridgeRef.current?.openLibrary()}>
        Library
      </button>
      <button type="button" className="toolbar-btn" onClick={() => bridgeRef.current?.saveChat()}>
        Save
      </button>
      <button
        type="button"
        ref={pinsAnchorRef}
        className={"toolbar-btn toolbar-btn-checkable" + (state.pinsChecked ? " checked" : "")}
        aria-pressed={state.pinsChecked}
        title="Show navigation pins"
        onClick={() => bridgeRef.current?.togglePins()}
      >
        Pins
      </button>
      <button type="button" className="toolbar-btn" onClick={() => bridgeRef.current?.organizeNodes()}>
        Organize
      </button>

      <span className="toolbar-separator" />

      <button type="button" className="toolbar-btn" onClick={() => bridgeRef.current?.zoomIn()}>
        Zoom In
      </button>
      <button type="button" className="toolbar-btn" onClick={() => bridgeRef.current?.zoomOut()}>
        Zoom Out
      </button>

      <span className="toolbar-separator" />

      <button type="button" className="toolbar-btn" onClick={() => bridgeRef.current?.resetZoom()}>
        Reset
      </button>
      <button type="button" className="toolbar-btn" onClick={() => bridgeRef.current?.fitAll()}>
        Fit All
      </button>
      <button
        type="button"
        className={"toolbar-btn toolbar-btn-checkable" + (controlsChecked ? " checked" : "")}
        aria-pressed={controlsChecked}
        onClick={onToggleControls}
      >
        Controls
      </button>

      <button
        type="button"
        ref={pluginsAnchorRef}
        className="toolbar-btn toolbar-btn-plugins"
        onClick={() => bridgeRef.current?.togglePlugins()}
      >
        Plugins <span className="toolbar-chevron">&#9662;</span>
      </button>

      <span className="toolbar-spacer" />

      <select
        className="toolbar-mode-select"
        value={state.currentMode}
        aria-label="Provider mode"
        onChange={(event) => bridgeRef.current?.selectMode(event.target.value)}
      >
        {state.modeOptions.map((mode) => (
          <option key={mode} value={mode}>
            {mode}
          </option>
        ))}
      </select>

      <button
        type="button"
        ref={settingsAnchorRef}
        className="toolbar-btn"
        onClick={() => bridgeRef.current?.openSettings()}
      >
        Settings
      </button>
      <button type="button" className="toolbar-btn" onClick={() => bridgeRef.current?.openAbout()}>
        About
      </button>
      <button
        type="button"
        ref={helpAnchorRef}
        className="toolbar-btn"
        onClick={() => bridgeRef.current?.openHelp()}
      >
        Help
      </button>
    </div>
  );
}

export default App;
