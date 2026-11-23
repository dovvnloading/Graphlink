import { ReactFlowProvider } from "@xyflow/react";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { validateAppSettingsState } from "../lib/bridge-core/generated/app-settings-state";
import type { AppSettingsState } from "../lib/bridge-core/generated/app-settings-state";
import { ConnectionStatus, WsTransport, defaultWsUrl } from "../lib/ws/transport";
import { applyTheme } from "./applyTheme";
import { getAnnouncement, subscribeAnnouncer } from "./announcer";
import { connectionBadgeLabel } from "./connectionBadge";
import { registerIntentErrors } from "./intentErrors";
import { Icon } from "./ui/icons/Icon";
import { StatusPill } from "./ui/Badge";
import { CanvasSearchProvider } from "./canvas/CanvasSearchContext";
import { ExecutionLimitsProvider } from "./canvas/ExecutionLimitsContext";
import { SceneStore } from "./canvas/sceneStore";
import { DocumentViewPanel } from "./canvas/DocumentViewPanel";
import type { ChatVersionCompareTarget } from "./canvas/ChatVersionCompareDialog";
import type { KnowledgeCitationSource } from "./canvas/ChatCitationChips";
import type { DocumentCitationTarget } from "./canvas/DocumentViewMarkdown";
import { AppBar } from "./chrome/AppBar";
import { Composer } from "./chrome/Composer";
import { ComposerStore } from "./chrome/composerStore";
import { ModelCapabilityProvider } from "./context/ModelCapabilityContext";
import { NotificationToaster } from "./chrome/NotificationBanner";
import { ConfirmDialog } from "./ui/ConfirmDialog";
import { OnboardingDialog } from "./chrome/OnboardingDialog";
import { OverlayProvider, useOverlays } from "./overlays/overlays";

// ADR-011 stage 11.6: ChatLibraryDialog, HelpDialog (+ its 76-item static
// help-data content), and SettingsDialog are the three heaviest "behind a
// click" chrome surfaces - none of them can contribute to first paint, so
// none of them belongs in the initial chunk. React.lazy splits each into
// its own chunk, fetched on demand; LazySurface below (not just Suspense)
// is what makes "on demand" mean "the user actually opened it" rather than
// "React attempted to render it," see that component's own comment.
// The read-only inspector is a separate chunk, keeping the 512 KB shell budget.
// The canvas is an interactive surface, not part of the static application shell.
// Fetch its React Flow renderer on first mount; the shared size helper stays eager.
const SceneCanvas = lazy(() => import("./canvas/SceneCanvas").then((m) => ({ default: m.SceneCanvas })));
const ChatVersionCompareDialog = lazy(() =>
  import("./canvas/ChatVersionCompareDialog").then((m) => ({ default: m.ChatVersionCompareDialog })),
);
const GlobalShortcuts = lazy(() =>
  import("./chrome/GlobalShortcuts").then((m) => ({ default: m.GlobalShortcuts })),
);
const ContextInspectorLauncher = lazy(() =>
  import("./context/ContextInspectorLauncher").then((m) => ({ default: m.ContextInspectorLauncher })),
);
const ChatLibraryDialog = lazy(() =>
  import("./chrome/ChatLibraryDialog").then((m) => ({ default: m.ChatLibraryDialog })),
);
const HelpDialog = lazy(() => import("./chrome/HelpDialog").then((m) => ({ default: m.HelpDialog })));
const SettingsDialog = lazy(() => import("./chrome/SettingsDialog").then((m) => ({ default: m.SettingsDialog })));
// ADR-019's 512,000-byte initial-chunk budget: every other surface that only
// a click or a shortcut opens, on the same LazySurface terms. Each was
// checked for work it does while CLOSED, which a deferred mount would
// silently stop: none has any. OnboardingDialog is the one that does (it
// opens itself on a fresh machine), so it stays eager - see its mount below.
// The topic-subscribing ones are safe mounted late: About and Diagnostics
// are their topic's only subscriber, so their first subscribe still fetches
// a snapshot, and QuickSwitcher already resubscribes for exactly that reason.
const AboutDialog = lazy(() => import("./chrome/AboutDialog").then((m) => ({ default: m.AboutDialog })));
const BuilderLaunchDialog = lazy(() =>
  import("./chrome/BuilderLaunchDialog").then((m) => ({ default: m.BuilderLaunchDialog })),
);
const CommandPalette = lazy(() => import("./chrome/CommandPalette").then((m) => ({ default: m.CommandPalette })));
const OrganizationDialog = lazy(() => import("./chrome/OrganizationDialog").then((m) => ({ default: m.OrganizationDialog })));
const GraphRunnerDialog = lazy(() => import("./chrome/GraphRunnerDialog").then((m) => ({ default: m.GraphRunnerDialog })));
const DiagnosticsDialog = lazy(() =>
  import("./chrome/DiagnosticsDialog").then((m) => ({ default: m.DiagnosticsDialog })),
);
const GlobalSearchDialog = lazy(() =>
  import("./chrome/GlobalSearchDialog").then((m) => ({ default: m.GlobalSearchDialog })),
);
const HarnessLaunchDialog = lazy(() =>
  import("./chrome/HarnessLaunchDialog").then((m) => ({ default: m.HarnessLaunchDialog })),
);
const KnowledgeSearchDialog = lazy(() =>
  import("./chrome/KnowledgeSearchDialog").then((m) => ({ default: m.KnowledgeSearchDialog })),
);
const QuickSwitcherDialog = lazy(() =>
  import("./chrome/QuickSwitcherDialog").then((m) => ({ default: m.QuickSwitcherDialog })),
);
const SearchOverlay = lazy(() => import("./chrome/SearchOverlay").then((m) => ({ default: m.SearchOverlay })));
const PinOverlay = lazy(() => import("./chrome/PinOverlay").then((m) => ({ default: m.PinOverlay })));
const PluginPicker = lazy(() => import("./chrome/PluginPicker").then((m) => ({ default: m.PluginPicker })));
const ViewPopover = lazy(() => import("./chrome/ViewPopover").then((m) => ({ default: m.ViewPopover })));

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

/**
 * ADR-011 stage 11.6: mounts a lazy chrome dialog once - and only once - its
 * own overlay surface has genuinely been opened. Wrapping a `React.lazy`
 * component in `<Suspense>` alone is not enough here: these three dialogs
 * were, and still are, rendered unconditionally in App's own JSX (Dialog's
 * `isOpen` check happens *inside* SettingsDialog/ChatLibraryDialog/
 * HelpDialog, via the shared `<Dialog>` wrapper, which returns null while
 * closed but is still a child these components render every time App
 * renders). If this component just returned `<Suspense><Settings.../></Sus>`
 * unconditionally, React would attempt to render the lazy child on App's
 * very first mount regardless of whether Settings was ever opened, firing
 * the dynamic import (and the network fetch behind it) moments after page
 * load - splitting the JS but not deferring the fetch, which is the whole
 * point of code-splitting something the user may never open this session.
 *
 * `hasOpened` latches true the first time the surface opens and never resets
 * - closing the dialog again does not unmount it, so its own internal state
 * (draft form fields, list scroll position, search text) survives being
 * closed and reopened exactly as it did when these were eagerly mounted.
 */
function LazySurface({
  overlayName,
  children,
  fallback = <LazyDialogFallback />,
}: {
  overlayName: string;
  children: ReactNode;
  /** What shows while the chunk loads. The dialog-shaped default suits a
   * dialog; a popover passes null rather than flash a full scrim for the
   * few milliseconds a local chunk takes. */
  fallback?: ReactNode;
}) {
  const overlays = useOverlays();
  const isOpen = overlays.isOpen(overlayName);
  const [hasOpened, setHasOpened] = useState(isOpen);
  // Latch true the moment `isOpen` first flips true. Adjusted during render
  // (React's documented pattern for state derived from a prop/subscription
  // change) rather than in a useEffect, so there is no extra post-commit
  // render pass: the setState call below is skipped on every render once
  // `hasOpened` is already true, so it only ever fires once.
  if (isOpen && !hasOpened) {
    setHasOpened(true);
  }
  if (!hasOpened) return null;
  return <Suspense fallback={fallback}>{children}</Suspense>;
}

/** Minimal Suspense fallback for the first-open chunk fetch above - same
 * scrim/panel shell as `Dialog` itself (overlays.tsx) so the swap-in once
 * the real dialog resolves doesn't jump the layout, just fills it in. */
function LazyDialogFallback() {
  return (
    <div className="overlay-scrim">
      <div className="overlay-dialog overlay-dialog-loading" role="status" aria-live="polite">
        Loading…
      </div>
    </div>
  );
}

/**
 * ADR-012 stage 12.3: the very first focusable element in the page, per the
 * standard skip-link convention - invisible until it itself receives focus
 * (Tab from anywhere before the canvas lands here first). Jumps a keyboard
 * user straight to the composer, bypassing every node on the canvas in one
 * activation instead of N individual Tab presses (React Flow's own node
 * wrapper is a real tab stop per node - see NodeMenu.tsx's own stage-12.3
 * doc for why that's not changed here). Exported (rather than left inline in
 * App's own JSX) so it can be exercised in isolation without booting the
 * whole shell's WS transport/stores - see App.test.tsx.
 *
 * A plain `<a href="#composer-message-input">` here (the original stage-12.3
 * shape, and the fix-security-pass finding key
 * skip-link-clobbers-token-fragment) is a same-document fragment navigation,
 * and the browser does NOT let React intercept a bare anchor click -
 * activating it sets `location.hash` to the literal string
 * "composer-message-input". lib/auth/token.ts reads that exact hash fresh on
 * every /api and /ws call to recover the per-launch capability token
 * graphlink_desktop.py wrote there (`#token=...`); once the hash is
 * overwritten the token is gone for the rest of the window's life (there is
 * no reload path in the WebView2 shell), and every image fetch / Copy /
 * Export Image / chart export after that silently 401s. Since this is the
 * FIRST tab stop on the page, any keyboard user hits it immediately. A
 * <button> gets the identical accessible "move focus to the composer"
 * behavior via a direct element.focus() call, without ever touching
 * location.hash - .skip-link's CSS (base.css) is class-selected, not
 * tag-selected, so the focus-reveal styling is unaffected by the tag swap.
 */
export function SkipToComposerLink() {
  return (
    <button
      type="button"
      className="skip-link"
      style={{ font: "inherit", cursor: "pointer" }}
      onClick={() => {
        document.getElementById("composer-message-input")?.focus();
      }}
    >
      Skip to message composer
    </button>
  );
}

function AppShell() {
  const overlays = useOverlays();
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
  const [documentViewCitation, setDocumentViewCitation] = useState<DocumentCitationTarget | null>(null);
  const [isDocumentViewOpen, setIsDocumentViewOpen] = useState(false);
  const [compareTarget, setCompareTarget] = useState<ChatVersionCompareTarget | null>(null);
  const nextCompareRequestId = useRef(0);
  const onCompareChatVersions = useCallback((nodeId: string, count: number, selectedIndex: number, isUser: boolean) => {
    if (count < 2) return;
    setCompareTarget({ nodeId, versionCount: count, selectedIndex, isUser, requestId: ++nextCompareRequestId.current });
    overlays.open("chat-version-compare", "dialog");
  }, [overlays]);
  // The composer row's HUD slot: the canvas portals its zoom dock and minimap
  // here so they share the composer card's baseline (see workspace.css).
  const [canvasHud, setCanvasHud] = useState<HTMLDivElement | null>(null);
  const transport = useMemo(() => new WsTransport(defaultWsUrl()), []);
  const sceneStore = useMemo(() => new SceneStore(transport), [transport]);
  const latestDocumentViewRequest = useRef(0);
  const composerStore = useMemo(() => new ComposerStore(transport), [transport]);

  const onOpenDocumentView = useCallback((
    markdown: string, sourceLabel: string, citation?: KnowledgeCitationSource,
  ) => {
    // Each selection supersedes any earlier read, including after the panel
    // closes or a different chat node opens. Document View remains the
    // existing shared reader, never a second citation-specific modal.
    const requestId = ++latestDocumentViewRequest.current;
    sceneStore.cancelWorkspacePreview();
    setDocumentViewCitation(null);
    setDocumentViewSourceLabel(sourceLabel);
    setDocumentViewContent(citation ? "Opening cited source…" : markdown);
    setIsDocumentViewOpen(true);
    if (!citation) return;

    void transport.request("knowledge", "openCitation", [
      citation.documentId, citation.chunkId, citation.citationId,
    ]).then((value) => {
      if (requestId !== latestDocumentViewRequest.current) return;
      const payload = value as { document?: {
        title: string; content: string; offsetStart: number; offsetEnd: number;
      } | null };
      const source = payload?.document;
      if (!source || typeof source.content !== "string" || typeof source.title !== "string" ||
          !Number.isSafeInteger(source.offsetStart) || !Number.isSafeInteger(source.offsetEnd)) {
        setDocumentViewContent("This cited source is no longer available in the current workspace.");
        return;
      }
      setDocumentViewContent(source.content);
      setDocumentViewSourceLabel(source.title);
      setDocumentViewCitation({
        citationId: citation.citationId,
        offsetStart: source.offsetStart,
        offsetEnd: source.offsetEnd,
      });
    }).catch(() => {
      if (requestId === latestDocumentViewRequest.current) {
        setDocumentViewContent("Unable to open the cited source.");
      }
    });
  }, [sceneStore, transport]);
  const onCloseDocumentView = useCallback(() => {
    ++latestDocumentViewRequest.current;
    sceneStore.cancelWorkspacePreview();
    setIsDocumentViewOpen(false);
  }, [sceneStore]);
  // ADR-018 stage 18.3: "Pin to Current Model" reads the Composer's live
  // route at CLICK time (getComposer() is a stable per-instance getter, not
  // a subscription) - see SceneCanvas.tsx's toFlowNodes own comment on why
  // this stays a getter, never a value threaded down.
  const getComposerRoute = useCallback(() => {
    const { provider, modelId } = composerStore.getComposer().route;
    return { provider, modelId };
  }, [composerStore]);

  // ADR-012 stage 12.3: the one aria-live announcer region's own text - see
  // announcer.ts's own doc for why this reads a module-level store instead
  // of sceneStore/composerStore directly (both write into it, and neither
  // imports the other).
  const announcement = useSyncExternalStore(subscribeAnnouncer, getAnnouncement);

  useEffect(() => {
    const offStatus = transport.onStatus(setStatus);
    const offIntentErrors = registerIntentErrors(transport);
    const offSystem = transport.subscribe("system", (payload) => {
      setSystem(payload as SystemState);
    });
    const offSettings = transport.subscribe("app-settings", (payload) => {
      // Same validate-before-trust discipline as every other subscriber -
      // this one only reads a single boolean, but an unvalidated cast here
      // was the one inconsistent gap in the pattern.
      const validated = validateAppSettingsState(payload);
      if (validated.ok) {
        setSettingsVisibility({ showTokenCounter: (validated.value as AppSettingsState).showTokenCounter });
        applyTheme((validated.value as AppSettingsState).theme);
      } else {
        console.error("[app-settings] rejected snapshot:", validated.errors);
      }
    });
    sceneStore.connect();
    composerStore.connect();
    transport.connect();
    return () => {
      offStatus();
      offIntentErrors();
      offSystem();
      offSettings();
      sceneStore.dispose();
      composerStore.dispose();
      transport.dispose();
    };
  }, [transport, sceneStore, composerStore]);

  return (
    <ReactFlowProvider>
        <Suspense fallback={null}>
          <GlobalShortcuts store={sceneStore} />
        </Suspense>
        {/* data-connection-status: the real WS connection state, always
            present regardless of whether the topbar renders any visual
            indicator for it (see .app-topbar-status below - a healthy
            connection now renders nothing there by design). The E2E suite's
            shared boot helper (e2e/helpers.ts) waits on this attribute to
            know the real backend round-trip has completed before touching
            anything, which it can no longer do by waiting for a badge that
            is often absent on purpose. Not a UI affordance - never styled,
            never meant to be seen - so this stays a plain attribute rather
            than a class, the same "invisible hook, not a rendered element"
            posture as aria-live regions elsewhere in this file. */}
        <div className="app-shell" data-connection-status={status}>
          <SkipToComposerLink />
          {/* ADR-012 stage 12.3: the one aria-live region for streaming
              start/finish and per-node run status - see announcer.ts. Always
              mounted (not gated behind any dialog/overlay) so a transition
              that fires while, say, Settings is open is never silently
              dropped. Visually hidden, not display:none - a display:none
              live region is never announced by any screen reader. */}
          <div aria-live="polite" role="status" className="visually-hidden">
            {announcement}
          </div>
          {/* Three declared grid tracks - brand, toolbar, status - so the
              regions of a permanently-visible bar have fixed homes and
              cannot encroach on one another. See .app-topbar in styles.css
              and AppBar.tsx's own layout contract. */}
          <header className="app-topbar">
            <div className="app-topbar-brand">
              <Icon name="brand" size="lg" />
              <span className="app-title">Graphlink</span>
            </div>
            <AppBar store={sceneStore} />
            <div className="app-topbar-status">
              {status !== "open" && (
                <span className={"app-conn app-conn-" + status} role="status"
                  aria-label={connectionBadgeLabel(status)}
                  data-backend-version={system.backendVersion}>
                  <span aria-hidden="true">
                    <StatusPill status={status === "closed" ? "Failed" : "Running"} />
                  </span>
                  <span>{connectionBadgeLabel(status)}</span>
                </span>
              )}
            </div>
          </header>

          <main className="app-canvas-region">
            <div className="app-canvas-layout-row">
              <DocumentViewPanel
                isOpen={isDocumentViewOpen}
                content={documentViewContent}
                sourceLabel={documentViewSourceLabel}
                citation={documentViewCitation}
                onClose={onCloseDocumentView}
              />
              <div className="app-canvas-content">
                {/* ADR-012 stage 12.6: widened from "wraps just SceneCanvas"
                    to "wraps this whole content region" so SettingsDialog's
                    own read-only Resource Limits section (below) can reach
                    the same useExecutionLimits() the code-execution approval
                    gate uses - a Context.Provider renders no DOM of its own,
                    so this costs nothing layout-wise, and every other child
                    here simply ignores a Context it doesn't consume. */}
                <ExecutionLimitsProvider transport={transport}>
                  <ModelCapabilityProvider store={composerStore}>
                  <div className="graph-stage">
                    {/* ADR-012 stage 12.5: wraps both the canvas (NodeMarkdown's
                        own search-highlighting read) and SearchOverlay (the
                        query's only writer) - see CanvasSearchContext.tsx. */}
                    <CanvasSearchProvider>
                      <Suspense fallback={
                        <div className="scene-canvas" role="status" aria-busy="true"
                          style={{ display: "grid", placeItems: "center" }}>Loading canvas…</div>
                      }>
                        <SceneCanvas
                          store={sceneStore}
                          onOpenDocumentView={onOpenDocumentView}
                          onCompareChatVersions={onCompareChatVersions}
                          getComposerRoute={getComposerRoute}
                          hudContainer={canvasHud}
                        />
                      </Suspense>
                      <div className="app-search-layer">
                        <LazySurface overlayName="search" fallback={null}>
                          <SearchOverlay store={sceneStore} />
                        </LazySurface>
                      </div>
                    </CanvasSearchProvider>
                    <LazySurface overlayName="pins" fallback={null}>
                      <PinOverlay store={sceneStore} />
                    </LazySurface>
                    <LazySurface overlayName="view" fallback={null}>
                      <ViewPopover store={sceneStore} />
                    </LazySurface>
                    <LazySurface overlayName="plugins" fallback={null}>
                      <PluginPicker transport={transport} store={sceneStore} />
                    </LazySurface>
                    <NotificationToaster store={composerStore} />
                    <ConfirmDialog />
                  </div>
                  {/* The canvas HUD slot sits beside the composer footer, not
                      inside its landmark, and overlays the same row. */}
                  <div className="app-composer-row">
                    <footer className="app-composer-layer" aria-label="Message composer">
                      <Composer
                        store={composerStore}
                        sceneStore={sceneStore}
                        showTokenCounter={settingsVisibility.showTokenCounter !== false}
                        contextInspector={(useKnowledge, branchNodeId) => (
                          <Suspense fallback={null}>
                            <ContextInspectorLauncher
                              sceneStore={sceneStore} composerStore={composerStore} transport={transport}
                              useKnowledge={useKnowledge} targetNodeId={branchNodeId}
                            />
                          </Suspense>
                        )}
                      />
                    </footer>
                    <div className="app-canvas-hud" ref={setCanvasHud} />
                  </div>
                  <LazySurface overlayName="palette">
                    <CommandPalette store={sceneStore} />
                  </LazySurface>
                  <LazySurface overlayName="organize">
                    <OrganizationDialog store={sceneStore} />
                  </LazySurface>
                  <LazySurface overlayName="graph-runner">
                    <GraphRunnerDialog store={sceneStore} />
                  </LazySurface>
                  <LazySurface overlayName="quick-switcher">
                    <QuickSwitcherDialog transport={transport} />
                  </LazySurface>
                  <LazySurface overlayName="about">
                    <AboutDialog transport={transport} />
                  </LazySurface>
                  {/* ADR-012 stage 12.6: not lazy, unlike Help/Settings/
                      Library just below - it has to be mounted from the
                      start so its own app-settings subscription can decide
                      whether to auto-open itself on a fresh machine; nothing
                      else opens it first the way a user click opens those
                      three. */}
                  <OnboardingDialog transport={transport} store={sceneStore} />
                  <LazySurface overlayName="help">
                    <HelpDialog />
                  </LazySurface>
                  <LazySurface overlayName="diagnostics">
                    <DiagnosticsDialog transport={transport} />
                  </LazySurface>
                  <LazySurface overlayName="knowledge">
                    <KnowledgeSearchDialog transport={transport} onOpenDocumentView={onOpenDocumentView} />
                  </LazySurface>
                  <LazySurface overlayName="global-search">
                    <GlobalSearchDialog transport={transport} />
                  </LazySurface>
                  <LazySurface overlayName="builder-launch">
                    <BuilderLaunchDialog transport={transport} store={sceneStore} />
                  </LazySurface>
                  <LazySurface overlayName="harness-launch">
                    <HarnessLaunchDialog transport={transport} store={sceneStore} />
                  </LazySurface>
                  <LazySurface overlayName="settings">
                    <SettingsDialog transport={transport} />
                  </LazySurface>
                  <LazySurface overlayName="library">
                    <ChatLibraryDialog transport={transport} store={sceneStore} />
                  </LazySurface>
                  <LazySurface overlayName="chat-version-compare">
                    {compareTarget && (
                      <ChatVersionCompareDialog key={compareTarget.requestId} store={sceneStore} target={compareTarget} />
                    )}
                  </LazySurface>
                  </ModelCapabilityProvider>
                </ExecutionLimitsProvider>
              </div>
            </div>
          </main>
        </div>
    </ReactFlowProvider>
  );
}

function App() {
  return <OverlayProvider><AppShell /></OverlayProvider>;
}

export default App;
