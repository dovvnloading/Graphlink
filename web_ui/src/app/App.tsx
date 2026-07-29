import { ReactFlowProvider, useReactFlow } from "@xyflow/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { TOPIC_VALIDATORS } from "../lib/api-contract/topics";
import type { AppSettingsState } from "../lib/bridge-core/generated/app-settings-state";
import { isTextEditable } from "../lib/bridge-core/textFocus";
import { ConnectionStatus, WsTransport, defaultWsUrl } from "../lib/ws/transport";
import { SceneCanvas, measuredNodeSize } from "./canvas/SceneCanvas";
import { SceneStore } from "./canvas/sceneStore";
import { resolveTreeNavigationTarget, type TreeNavigationDirection } from "./canvas/treeNavigation";
import { requestNewChat } from "./chrome/commands";
import { isGatedWhileTyping, resolveShortcut, type ShortcutId } from "./chrome/shortcuts";
import { DocumentViewPanel } from "./canvas/DocumentViewPanel";
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

/**
 * The global keyboard shortcuts (Qt-removal plan R7.5c) - the SPA successor
 * to legacy's QShortcut block (graphlink_window.py:307-318) and its
 * AcceleratorForwardingFilter typing arbitration. Key matching and the
 * suppression rule are pure functions in chrome/shortcuts.ts; branch
 * traversal is pure in canvas/treeNavigation.ts. This component owns only
 * the dispatch, which needs the live store/overlays/React Flow instance.
 *
 * Lives inside BOTH OverlayProvider and ReactFlowProvider (see App's tree),
 * so useOverlays() and useReactFlow() are both legal here.
 *
 * R2 shipped Ctrl+K/Ctrl+F here ungated; the recon of legacy's
 * GATED_SHORTCUTS confirmed Ctrl+F must be suppressed while typing and
 * Ctrl+K must NOT be - both now go through the same table as the rest.
 */
function GlobalShortcuts({ store }: { store: SceneStore }) {
  const overlays = useOverlays();
  const reactFlow = useReactFlow();

  useEffect(() => {
    function navigate(direction: TreeNavigationDirection) {
      const scene = store.getScene();
      // store.getSelectedNodeId() mirrors React Flow's own selection, but
      // only ever tracks ONE id - so re-derive from React Flow to honor
      // legacy's "exactly one item selected" precondition, which a
      // multi-selection must fail.
      const selected = reactFlow.getNodes().filter((n) => n.selected);
      const currentId = selected.length === 1 ? selected[0].id : null;
      const targetId = resolveTreeNavigationTarget(scene, currentId, direction);
      if (!targetId) return; // every legacy boundary is a pure no-op

      // Legacy clears the whole selection, selects the target, and centers
      // the view on it (instant, zoom unchanged). Selecting through
      // setNodes also flows out via onSelectionChange -> the store's
      // selected-node mirror, so the composer's context anchor follows.
      reactFlow.setNodes((nodes) => nodes.map((n) => ({ ...n, selected: n.id === targetId })));
      const target = scene.nodes.find((n) => n.id === targetId);
      if (!target) return;
      // Legacy centerOn() takes the item's bounding-rect CENTER, so the node
      // half-size matters. It has to come from measuredNodeSize: React Flow's
      // internal `measured` is empty here far more often than not (every
      // scene snapshot rebuilds the node objects it is derived from - see
      // that helper's own comment), and a naive `?? 0` would silently degrade
      // to centering on the top-left corner, throwing the target half off
      // screen at high zoom. Only a node absent from the DOM lands on the
      // zero fallback, which is the legacy no-size behavior anyway.
      const size = measuredNodeSize(reactFlow, targetId) ?? { width: 0, height: 0 };
      reactFlow.setCenter(target.x + size.width / 2, target.y + size.height / 2, {
        zoom: reactFlow.getZoom(),
      });
    }

    function dispatch(id: ShortcutId) {
      switch (id) {
        case "new-chat":
          return requestNewChat(store);
        case "toggle-library":
          // Legacy's Ctrl+L is overlay_manager.toggle("library"), not open.
          return overlays.toggle("library", "dialog");
        case "save-chat":
          return store.saveChat();
        case "create-frame":
          // Legacy createFrame/createContainer need only ONE eligible node
          // (graphlink_scene.py:689-730) and silently no-op on zero - the
          // shortcuts match that exactly. The palette's own Create Frame
          // entry keeps its stricter 2+ gate; see the ledger note.
          return applyGrouping("frame");
        case "create-container":
          return applyGrouping("container");
        case "compare-branches":
          return applyCompareBranches();
        case "synthesize-branches":
          return applySynthesizeBranches();
        case "toggle-palette":
          return overlays.toggle("palette", "dialog");
        case "toggle-search":
          return overlays.toggle("search", "popover");
        case "navigate-up":
          return navigate("up");
        case "navigate-down":
          return navigate("down");
        case "navigate-left":
          return navigate("left");
        case "navigate-right":
          return navigate("right");
      }
    }

    function applyGrouping(kind: "frame" | "container") {
      const ids = reactFlow.getNodes().filter((n) => n.selected).map((n) => n.id);
      if (ids.length === 0) return; // legacy: bare return, no message
      if (kind === "frame") store.createFrame(ids);
      else store.createContainer(ids);
    }

    // ADR-002 Workstream 1 ("Compare Branches"): same selection-gathering
    // shape as applyGrouping above, but forwards even a single selected id
    // rather than bare-returning on anything short of the real minimum -
    // compare_branches's own backend validation shows an informative
    // notification ("Select at least 2 branches to compare") for that near-
    // miss case, which is more helpful than legacy's silent "nothing
    // selected" convention when the user very clearly attempted a real
    // action (bare-returning is still correct for the genuine zero-selected
    // case - nothing to give feedback about there).
    function applyCompareBranches() {
      const ids = reactFlow.getNodes().filter((n) => n.selected).map((n) => n.id);
      if (ids.length === 0) return;
      store.compareBranches(ids);
    }

    // ADR-002 Workstream 1 ("Synthesize Branches"): STAGES the selection
    // rather than firing an intent immediately (unlike applyCompareBranches
    // above) - synthesis needs the user's own free-text instructions first,
    // gathered by the Composer's very next Send (see sceneStore.
    // setSynthesizeTargetNodeIds's own comment).
    //
    // UNLIKE applyCompareBranches/applyGrouping, this function DOES
    // duplicate synthesize_branches's own "2+ ids, every one a real chat
    // node" backend validation here, client-side, rather than deferring to
    // it - a deliberate divergence from this file's usual "let the backend
    // validate" posture. The reason: an invalid selection here doesn't just
    // fail an immediate, nothing-lost action (Compare Branches' own near-
    // miss case) - it stages a pending synthesis that the user then types
    // real, possibly substantial instructions against. Without this check,
    // pressing Send on that staged-but-invalid selection fires
    // synthesizeBranches, and sceneStore.sendMessage/Composer.tsx's send()
    // both optimistically clear the staged selection and the draft text
    // immediately (the same fire-and-forget posture every WS intent in this
    // app uses) - by the time the backend's own rejection notification
    // arrives, the user's typed instructions are already gone with no
    // recovery. Catching the same 2-invalid-selection cases HERE, before
    // any instructions get typed, closes that hole entirely. The backend's
    // own validation stays exactly as-is (defense in depth for any other
    // caller), this is additive.
    function applySynthesizeBranches() {
      const selected = reactFlow.getNodes().filter((n) => n.selected);
      if (selected.length === 0) return; // legacy-style bare return: nothing attempted, nothing to report
      const allChat = selected.every((n) => n.type === "chat");
      const ids = selected.map((n) => n.id);
      if (!allChat) {
        store.showInfoNotification("Every selected node must be a real chat message to synthesize.");
        return;
      }
      if (ids.length < 2) {
        store.showInfoNotification("Select at least 2 branches to synthesize.");
        return;
      }
      store.setSynthesizeTargetNodeIds(ids);
    }

    function onKeyDown(event: KeyboardEvent) {
      const id = resolveShortcut(event);
      if (!id) return;
      // The AcceleratorForwardingFilter port: while a text input has focus,
      // the gated shortcuts are left entirely alone so the keystroke reaches
      // the field, and only the exempt ones (Save, palette) still fire.
      if (isGatedWhileTyping(id) && isTextEditable(document.activeElement)) return;
      event.preventDefault();
      dispatch(id);
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [overlays, reactFlow, store]);

  return null;
}

function App() {
  const [status, setStatus] = useState<ConnectionStatus>("closed");
  const [system, setSystem] = useState<SystemState>({});
  // showTokenCounter defaults false (R8a: off by default, matching
  // SettingsManager.get_show_token_counter's own default) until the real
  // snapshot arrives, so the overlay doesn't flash visible for a user who
  // has it off.
  const [settingsVisibility, setSettingsVisibility] = useState<SettingsVisibilityState>({ showTokenCounter: false });

  // R8a follow-up: Open Document View's state lives here, not inside
  // SceneCanvas, because the panel is now a real docked layout sibling of
  // EVERYTHING in .app-canvas-region (composer/token-counter/search/etc
  // included, not just the canvas) - see .app-canvas-layout-row below and
  // DocumentViewPanel.tsx's own doc comment.
  const [documentViewContent, setDocumentViewContent] = useState<string | null>(null);
  const [documentViewSourceLabel, setDocumentViewSourceLabel] = useState<string | null>(null);
  const [isDocumentViewOpen, setIsDocumentViewOpen] = useState(false);
  const onOpenDocumentView = useCallback((markdown: string, sourceLabel: string) => {
    setDocumentViewContent(markdown);
    setDocumentViewSourceLabel(sourceLabel);
    setIsDocumentViewOpen(true);
  }, []);
  const onCloseDocumentView = useCallback(() => setIsDocumentViewOpen(false), []);

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
        <GlobalShortcuts store={sceneStore} />
        <div className="app-shell">
          <header className="app-topbar">
            <span className="app-title">Graphlink</span>
            <AppBar store={sceneStore} />
            <span className={`app-conn app-conn-${status}`} title={`backend ${system.backendVersion ?? ""}`}>
              {status === "open" ? "connected" : status}
            </span>
          </header>

          <main className="app-canvas-region">
            <div className="app-canvas-layout-row">
              <DocumentViewPanel
                isOpen={isDocumentViewOpen}
                content={documentViewContent}
                sourceLabel={documentViewSourceLabel}
                onClose={onCloseDocumentView}
              />
              <div className="app-canvas-content">
                <SceneCanvas store={sceneStore} onOpenDocumentView={onOpenDocumentView} />
                <div className="app-search-layer">
                  <SearchOverlay store={sceneStore} />
                </div>
                <PinOverlay store={sceneStore} />
                <ViewPopover store={sceneStore} />
                <PluginPicker transport={transport} store={sceneStore} />
                <div className="app-notification-layer">
                  <NotificationBanner store={composerStore} />
                </div>
                <div className="app-composer-layer">
                  <Composer
                    store={composerStore}
                    sceneStore={sceneStore}
                    showTokenCounter={settingsVisibility.showTokenCounter !== false}
                  />
                </div>
                <CommandPalette store={sceneStore} />
                <AboutDialog transport={transport} />
                <HelpDialog />
                <SettingsDialog transport={transport} />
                <ChatLibraryDialog transport={transport} />
              </div>
            </div>
          </main>
        </div>
      </ReactFlowProvider>
    </OverlayProvider>
  );
}

export default App;
