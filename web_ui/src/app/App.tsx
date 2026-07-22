import { useEffect, useMemo, useRef, useState } from "react";
import { ConnectionStatus, WsTransport, defaultWsUrl } from "../lib/ws/transport";

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
  const transportRef = useRef<WsTransport | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("closed");
  const [system, setSystem] = useState<SystemState>({});
  const [pingMs, setPingMs] = useState<number | null>(null);
  const [pingError, setPingError] = useState<string | null>(null);

  const transport = useMemo(() => {
    const t = new WsTransport(defaultWsUrl());
    transportRef.current = t;
    return t;
  }, []);

  useEffect(() => {
    const offStatus = transport.onStatus(setStatus);
    const offSystem = transport.subscribe("system", (payload) => {
      setSystem(payload as SystemState);
    });
    transport.connect();
    return () => {
      offStatus();
      offSystem();
      transport.dispose();
    };
  }, [transport]);

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
        <div className="app-canvas-placeholder">
          <p className="app-canvas-placeholder-title">Canvas</p>
          <p className="app-canvas-placeholder-body">React Flow node graph lands in R1.</p>
        </div>

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
