import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useMemo, useState } from "react";
import { ConnectionStatus, WsTransport, defaultWsUrl } from "../lib/ws/transport";
import { SceneCanvas } from "./canvas/SceneCanvas";
import { SceneStore } from "./canvas/sceneStore";
import { AppBar } from "./chrome/AppBar";
import { Composer } from "./chrome/Composer";
import { ComposerStore } from "./chrome/composerStore";
import { NotificationBanner } from "./chrome/NotificationBanner";
import { TokenCounter } from "./chrome/TokenCounter";
import { ViewPopover } from "./chrome/ViewPopover";
import { OverlayProvider } from "./overlays/overlays";

/**
 * The single-app shell (Qt-removal plan R0-R2).
 *
 * R0 laid the transport + layout; R1 put the React Flow canvas in the
 * middle; R2 replaces the placeholder header with the real app bar, mounts
 * the overlay system, and consolidates the chrome surfaces. The
 * ReactFlowProvider wraps the WHOLE shell so the app bar's viewport
 * controls and the canvas share one React Flow instance.
 */

interface SystemState {
  app?: string;
  backendVersion?: string;
  sessionId?: string;
  revision?: number;
}

function App() {
  const [status, setStatus] = useState<ConnectionStatus>("closed");
  const [system, setSystem] = useState<SystemState>({});
  const [pinsVisible, setPinsVisible] = useState(true);

  const transport = useMemo(() => new WsTransport(defaultWsUrl()), []);
  const sceneStore = useMemo(() => new SceneStore(transport), [transport]);
  const composerStore = useMemo(() => new ComposerStore(transport), [transport]);

  useEffect(() => {
    const offStatus = transport.onStatus(setStatus);
    const offSystem = transport.subscribe("system", (payload) => {
      setSystem(payload as SystemState);
    });
    sceneStore.connect();
    composerStore.connect();
    transport.connect();
    return () => {
      offStatus();
      offSystem();
      sceneStore.dispose();
      composerStore.dispose();
      transport.dispose();
    };
  }, [transport, sceneStore, composerStore]);

  return (
    <OverlayProvider>
      <ReactFlowProvider>
        <div className="app-shell">
          <header className="app-topbar">
            <span className="app-title">Graphlink</span>
            <AppBar
              store={sceneStore}
              pinsVisible={pinsVisible}
              onTogglePins={() => setPinsVisible((v) => !v)}
            />
            <span className={`app-conn app-conn-${status}`} title={`backend ${system.backendVersion ?? ""}`}>
              {status === "open" ? "connected" : status}
            </span>
          </header>

          <main className="app-canvas-region">
            <SceneCanvas store={sceneStore} pinsVisible={pinsVisible} />
            <div className="app-popover-layer">
              <ViewPopover store={sceneStore} />
            </div>
            <div className="app-token-counter-layer">
              <TokenCounter store={composerStore} />
            </div>
            <div className="app-notification-layer">
              <NotificationBanner store={composerStore} />
            </div>
          </main>

          <footer className="app-composer-region">
            <Composer store={composerStore} />
          </footer>
        </div>
      </ReactFlowProvider>
    </OverlayProvider>
  );
}

export default App;
