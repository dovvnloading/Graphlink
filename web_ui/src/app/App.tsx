import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useMemo, useState } from "react";
import { TOPIC_VALIDATORS } from "../lib/api-contract/topics";
import type { AppSettingsState } from "../lib/bridge-core/generated/app-settings-state";
import { ConnectionStatus, WsTransport, defaultWsUrl } from "../lib/ws/transport";
import { SceneCanvas } from "./canvas/SceneCanvas";
import { SceneStore } from "./canvas/sceneStore";
import { AboutDialog } from "./chrome/AboutDialog";
import { AppBar } from "./chrome/AppBar";
import { ChatLibraryDialog } from "./chrome/ChatLibraryDialog";
import { CommandPalette } from "./chrome/CommandPalette";
import { Composer } from "./chrome/Composer";
import { ComposerStore } from "./chrome/composerStore";
import { HelpDialog } from "./chrome/HelpDialog";
import { NotificationBanner } from "./chrome/NotificationBanner";
import { PinOverlay } from "./chrome/PinOverlay";
import { PluginPicker } from "./chrome/PluginPicker";
import { SearchOverlay } from "./chrome/SearchOverlay";
import { SettingsDialog } from "./chrome/SettingsDialog";
import { TokenCounter } from "./chrome/TokenCounter";
import { ViewPopover } from "./chrome/ViewPopover";
import { OverlayProvider, useOverlays } from "./overlays/overlays";

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

interface SettingsVisibilityState {
  showTokenCounter?: boolean;
}

// Ctrl/Cmd+K opens the command palette, Ctrl/Cmd+F the canvas search -
// the conventional bindings the legacy islands' own keyPressEvent handlers
// used. Lives inside OverlayProvider (needs useOverlays()), so it is its
// own small component rather than inline in App().
function GlobalShortcuts() {
  const overlays = useOverlays();
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const mod = event.ctrlKey || event.metaKey;
      if (mod && event.key.toLowerCase() === "k") {
        event.preventDefault();
        overlays.toggle("palette", "dialog");
      } else if (mod && event.key.toLowerCase() === "f") {
        event.preventDefault();
        overlays.toggle("search", "popover");
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [overlays]);
  return null;
}

function App() {
  const [status, setStatus] = useState<ConnectionStatus>("closed");
  const [system, setSystem] = useState<SystemState>({});
  // showTokenCounter defaults true (matches AppSettingsStatePayload's
  // default and the legacy AppearanceSettingsWidget's own initial state)
  // until the real snapshot arrives, so the overlay doesn't flash hidden.
  const [settingsVisibility, setSettingsVisibility] = useState<SettingsVisibilityState>({ showTokenCounter: true });

  const transport = useMemo(() => new WsTransport(defaultWsUrl()), []);
  const sceneStore = useMemo(() => new SceneStore(transport), [transport]);
  const composerStore = useMemo(() => new ComposerStore(transport), [transport]);

  useEffect(() => {
    const offStatus = transport.onStatus(setStatus);
    const offSystem = transport.subscribe("system", (payload) => {
      setSystem(payload as SystemState);
    });
    const offSettings = transport.subscribe("app-settings", (payload) => {
      // Same validate-before-trust discipline as every other subscriber -
      // this one only reads a single boolean, but an unvalidated cast here
      // was the one inconsistent gap in the pattern.
      const validated = TOPIC_VALIDATORS["app-settings"](payload);
      if (validated.ok) {
        setSettingsVisibility({ showTokenCounter: (validated.value as AppSettingsState).showTokenCounter });
      } else {
        console.error("[app-settings] rejected snapshot:", validated.errors);
      }
    });
    sceneStore.connect();
    composerStore.connect();
    transport.connect();
    return () => {
      offStatus();
      offSystem();
      offSettings();
      sceneStore.dispose();
      composerStore.dispose();
      transport.dispose();
    };
  }, [transport, sceneStore, composerStore]);

  return (
    <OverlayProvider>
      <ReactFlowProvider>
        <GlobalShortcuts />
        <div className="app-shell">
          <header className="app-topbar">
            <span className="app-title">Graphlink</span>
            <AppBar store={sceneStore} />
            <span className={`app-conn app-conn-${status}`} title={`backend ${system.backendVersion ?? ""}`}>
              {status === "open" ? "connected" : status}
            </span>
          </header>

          <main className="app-canvas-region">
            <SceneCanvas store={sceneStore} />
            <div className="app-search-layer">
              <SearchOverlay store={sceneStore} />
            </div>
            <div className="app-pins-layer">
              <PinOverlay store={sceneStore} />
            </div>
            <div className="app-popover-layer">
              <ViewPopover store={sceneStore} />
            </div>
            <div className="app-plugins-layer">
              <PluginPicker transport={transport} />
            </div>
            {settingsVisibility.showTokenCounter !== false && (
              <div className="app-token-counter-layer">
                <TokenCounter store={composerStore} />
              </div>
            )}
            <div className="app-notification-layer">
              <NotificationBanner store={composerStore} />
            </div>
            <CommandPalette store={sceneStore} />
            <AboutDialog transport={transport} />
            <HelpDialog />
            <SettingsDialog transport={transport} />
            <ChatLibraryDialog transport={transport} />
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
