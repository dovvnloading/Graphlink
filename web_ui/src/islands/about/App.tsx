import { useEffect, useRef, useState } from "react";
import { AboutState, initialAboutState } from "./bridgeTypes";
import { AboutBridge, BridgeRejection, createAboutBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

function App() {
  const [state, setState] = useState<AboutState>(initialAboutState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const bridgeRef = useRef<AboutBridge | null>(null);

  useEffect(() => {
    const bridge = createAboutBridge(setState, setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  // The legacy dialog was frameless title-bar-only (no context-help button,
  // native close button retained); this frameless WebIslandHost has no
  // native title bar at all, so Escape has to substitute for it explicitly.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        bridgeRef.current?.close();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  if (rejection) {
    return (
      <BridgeErrorState
        title="About is unavailable"
        rejection={rejection}
        className="about-shell bridge-error"
      />
    );
  }

  return (
    <div className="about-shell">
      <h1 className="about-title">{state.appName}</h1>
      <p className="about-version">Version {state.appVersion}</p>

      <div className="about-divider" />

      <div className="about-links">
        <p className="about-section-label">Project</p>
        <button
          type="button"
          className="about-link-btn"
          onClick={() => bridgeRef.current?.openExternal(state.repositoryUrl)}
        >
          Graphlink Repository
        </button>

        <p className="about-section-label about-section-label-spaced">Developed By</p>
        <p className="about-dev-name">{state.developerName}</p>
        <button
          type="button"
          className="about-link-btn"
          onClick={() => bridgeRef.current?.openExternal(state.developerWebsiteUrl)}
        >
          Personal Webpage
        </button>
        <button
          type="button"
          className="about-link-btn"
          onClick={() => bridgeRef.current?.openExternal(state.developerGithubUrl)}
        >
          Personal GitHub
        </button>
      </div>

      <div className="about-footer">
        <span className="about-copyright">{state.copyrightText}</span>
        <button type="button" className="about-close-btn" onClick={() => bridgeRef.current?.close()}>
          Close
        </button>
      </div>
    </div>
  );
}

export default App;
