import { useEffect, useMemo, useState } from "react";
import { ConnectionStatus, WsTransport, defaultWsUrl } from "../lib/ws/transport";
import { SceneCanvas } from "./canvas/SceneCanvas";
import { SceneStore } from "./canvas/sceneStore";

/**
 * The single-app shell (Qt-removal plan R0).
 *
 * R0 scope on purpose: layout regions where the real surfaces land in later
 * phases (R1 canvas, R2 chrome, R3 nodes), plus the live system panel that
 * proves the Python backend round-trip - the R0 acceptance criterion.
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
  const [pingMs, setPingMs] = useState<number | null>(null);
  const [pingError, setPingError] = useState<string | null>(null);

  const transport = useMemo(() => new WsTransport(defaultWsUrl()), []);
  const sceneStore = useMemo(() => new SceneStore(transport), [transport]);

  useEffect(() => {
    const offStatus = transport.onStatus(setStatus);
    const offSystem = transport.subscribe("system", (payload) => {
      setSystem(payload as SystemState);
    });
    sceneStore.connect();
    transport.connect();
    return () => {
      offStatus();
      offSystem();
      sceneStore.dispose();
      transport.dispose();
    };
  }, [transport, sceneStore]);

  async function ping() {
    setPingError(null);
    const started = performance.now();
    try {
      await transport.request("system", "ping", ["r0-acceptance"]);
      setPingMs(Math.round((performance.now() - started) * 10) / 10);
    } catch (error) {
      setPingMs(null);
      setPingError(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <div className="app-shell">
      <header className="app-topbar">
        <span className="app-title">Graphlink</span>
        <span className="app-topbar-note">app bar lands in R2</span>
        <span className={`app-conn app-conn-${status}`}>
          {status === "open" ? "backend connected" : status}
        </span>
      </header>

      <main className="app-canvas-region">
        <SceneCanvas store={sceneStore} />

        <section className="app-system-panel" aria-label="Backend status">
          <p className="app-system-title">SYSTEM</p>
          <dl className="app-system-rows">
            <div className="app-system-row">
              <dt>Backend</dt>
              <dd>{system.backendVersion ?? "—"}</dd>
            </div>
            <div className="app-system-row">
              <dt>Session</dt>
              <dd>{system.sessionId ?? "—"}</dd>
            </div>
            <div className="app-system-row">
              <dt>Revision</dt>
              <dd>{system.revision ?? "—"}</dd>
            </div>
          </dl>
          <button
            type="button"
            className="app-ping-button"
            onClick={ping}
            disabled={status !== "open"}
          >
            Ping backend
          </button>
          {pingMs !== null && <p className="app-ping-result">round-trip {pingMs} ms</p>}
          {pingError !== null && <p className="app-ping-error">{pingError}</p>}
        </section>
      </main>

      <footer className="app-composer-region">
        <div className="app-composer-placeholder">Composer dock lands in R2.</div>
      </footer>
    </div>
  );
}

export default App;
