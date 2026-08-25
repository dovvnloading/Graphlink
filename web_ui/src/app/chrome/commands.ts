import type { ReactFlowInstance } from "@xyflow/react";
import { applyCompareBranches, applySynthesizeBranches } from "../canvas/branchActions";
import { FIT_VIEW_MAX_ZOOM } from "../canvas/canvasConstants";
import { exportCanvasAsPng } from "../canvas/exportCanvasPng";
import type { SceneStore } from "../canvas/sceneStore";
import type { OverlayContextValue } from "../overlays/overlays";

/**
 * The command registry (Qt-removal plan R2.4) - command-palette's SPA
 * successor. graphlink_window_navigation.py registers ~25 commands; most
 * are node-type creation or per-node AI ops that need R3 (real node types)
 * or R4/R5 (agents, plugins) to mean anything real. Rather than fabricate
 * those, this registry lists only commands genuinely executable today
 * against the R1/R2 backend - it grows command-by-command as later phases
 * land real capability, the same explicit-defer discipline as the app bar's
 * disabled Save/provider-select.
 */

export interface PaletteCommand {
  id: string;
  name: string;
  aliases: string[];
  run: () => void;
  enabled: () => boolean;
}

/**
 * R7.5c: New Chat, with legacy's confirm step restored.
 * graphlink_window.py's new_chat (1427-1442) always showed a blocking
 * QMessageBox ("Start a new chat? Any unsaved changes will be lost.",
 * default No) before clearing the canvas, and only skipped it when there was
 * genuinely nothing to lose. R7.5a shipped the palette command without that
 * guard; both the palette and R7.5c's Ctrl+T now route through here so the
 * confirm can never apply to one surface and not the other.
 *
 * Legacy's skip condition is BOTH halves of "scene is empty AND there is no
 * current chat id" (graphlink_window.py:1429). The first draft of this used
 * only the empty-canvas half, which quietly inverted the guard in the unsafe
 * direction: emptying a loaded chat and pressing Ctrl+T skipped the confirm,
 * and newChat() drops current_chat_id, so the next Save would INSERT a new
 * row rather than update the loaded one - a silent detach from the chat the
 * user thought they were in. hasSavedChat (R7.5c) is the wire-side derivation
 * of current_chat_id added for exactly this predicate; the id itself stays
 * server-side.
 *
 * confirmFn is injectable purely so tests never touch a real modal; the
 * default is the browser's own blocking confirm, which matches legacy's
 * modal semantics (synchronous, blocks until answered) - the command
 * registry's run() is sync, so an async dialog could not be awaited here.
 */
export function requestNewChat(
  store: SceneStore,
  confirmFn: (message: string) => boolean = (message) => window.confirm(message),
): void {
  const scene = store.getScene();
  const nothingToLose = scene.nodes.length === 0 && !scene.hasSavedChat;
  if (!nothingToLose && !confirmFn("Start a new chat? Any unsaved changes will be lost.")) return;
  store.newChat();
}

export function buildCommands(
  store: SceneStore,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  rf: ReactFlowInstance<any, any>,
  overlays: OverlayContextValue,
): PaletteCommand[] {
  const hasNodes = () => store.getScene().nodes.length > 0;
  const hasSelection = () => rf.getNodes().some((n) => n.selected);
  // R6.1: Create Frame/Create Container both need 2+ currently-selected
  // canvas nodes - reuses rf.getNodes() the same way delete-selected already
  // does above, rather than sceneStore's own selectedNodeId (that field only
  // ever tracks ONE id - see its own doc comment on SceneStore - so it
  // cannot answer a "2+ selected" question).
  const selectedNodeIds = () => rf.getNodes().filter((n) => n.selected).map((n) => n.id);
  const hasMultiSelection = () => selectedNodeIds().length >= 2;
  // R7.5e: Collapse All/Expand All are gated on scene content, not selection
  // (legacy enabled them whenever at least one eligible node existed,
  // regardless of what was selected) - restricted to the three kinds the
  // backend's collapseAllNodes/expandAllNodes intents actually touch.
  const hasCollapsibleNodes = () =>
    store.getScene().nodes.some((n) => n.kind === "chat" || n.kind === "conversation" || n.kind === "html");

  return [
    {
      id: "fit-all",
      name: "Fit All to View",
      aliases: ["fit screen", "zoom fit"],
      run: () => rf.fitView({ duration: 200, maxZoom: FIT_VIEW_MAX_ZOOM }),
      enabled: hasNodes,
    },
    {
      id: "reset-view",
      name: "Reset View",
      aliases: ["reset zoom", "default view"],
      run: () => rf.setViewport({ ...rf.getViewport(), zoom: 1 }, { duration: 200 }),
      enabled: () => true,
    },
    {
      id: "export-canvas-png",
      name: "Export Canvas as PNG",
      aliases: ["export png", "download image", "save canvas image"],
      run: () => void exportCanvasAsPng(rf, "--gl-surface-window", (value) => store.setExportInProgress(value)),
      enabled: hasNodes,
    },
    {
      id: "zoom-in",
      name: "Zoom In",
      aliases: ["zoom in"],
      run: () => rf.zoomIn({ duration: 150 }),
      enabled: () => true,
    },
    {
      id: "zoom-out",
      name: "Zoom Out",
      aliases: ["zoom out"],
      run: () => rf.zoomOut({ duration: 150 }),
      enabled: () => true,
    },
    {
      id: "organize-nodes",
      name: "Organize Nodes",
      aliases: ["organize", "auto layout", "rearrange"],
      run: () => store.organizeNodes(),
      enabled: hasNodes,
    },
    {
      id: "select-all",
      name: "Select All Nodes",
      aliases: ["select all"],
      run: () => rf.setNodes((nodes) => nodes.map((n) => ({ ...n, selected: true }))) as void,
      enabled: hasNodes,
    },
    {
      id: "delete-selected",
      name: "Delete Selected Items",
      aliases: ["delete", "remove selected"],
      run: () => {
        const nodeIds = rf.getNodes().filter((n) => n.selected).map((n) => n.id);
        const edgeIds = rf.getEdges().filter((e) => e.selected).map((e) => e.id);
        store.removeNodes(nodeIds);
        store.removeEdges(edgeIds);
      },
      enabled: hasSelection,
    },
    // ADR-021 stage 21.5: ADR-002 Workstream 1's two branch agents were
    // reachable ONLY by keyboard shortcut - absent from this palette and
    // every menu, so two real capabilities were effectively undiscoverable.
    // Both delegate to canvas/branchActions.ts, the SAME implementation the
    // shortcut handler calls: synthesize in particular carries non-obvious
    // client-side validation (it stages a selection the user then types
    // instructions against), and a guard that drifted between the two
    // surfaces would be worse than having no palette entry at all.
    {
      id: "compare-branches",
      name: "Compare Branches",
      aliases: ["compare", "diff branches", "contrast branches"],
      run: () => applyCompareBranches(store, rf),
      enabled: hasMultiSelection,
    },
    {
      id: "synthesize-branches",
      name: "Synthesize Branches",
      aliases: ["synthesize", "merge branches", "combine branches"],
      run: () => applySynthesizeBranches(store, rf),
      enabled: hasMultiSelection,
    },
    {
      id: "add-pin",
      name: "Add Navigation Pin",
      aliases: ["create pin", "bookmark location", "pin current view"],
      run: () => {
        const viewport = rf.getViewport();
        const x = (window.innerWidth / 2 - viewport.x) / viewport.zoom;
        const y = (window.innerHeight / 2 - viewport.y) / viewport.zoom;
        store.addPin(`Pin ${store.getScene().pins.length + 1}`, x, y);
      },
      enabled: () => true,
    },
    {
      id: "open-library",
      name: "Open Library",
      aliases: ["chat library", "sessions"],
      run: () => overlays.open("library", "dialog"),
      enabled: () => true,
    },
    {
      id: "open-settings",
      name: "Open Settings",
      aliases: ["preferences", "config"],
      run: () => overlays.open("settings", "dialog"),
      enabled: () => true,
    },
    {
      id: "open-view",
      name: "Open View Controls",
      aliases: ["drag speed", "grid", "font"],
      run: () => overlays.open("view", "popover"),
      enabled: () => true,
    },
    {
      id: "open-pins",
      name: "Open Navigation Pins",
      aliases: ["pins list"],
      run: () => overlays.open("pins", "popover"),
      enabled: () => true,
    },
    {
      id: "open-help",
      name: "Open Help",
      aliases: ["docs", "shortcuts"],
      run: () => overlays.open("help", "dialog"),
      enabled: () => true,
    },
    {
      id: "open-about",
      name: "Open About",
      aliases: ["version", "credits"],
      run: () => overlays.open("about", "dialog"),
      enabled: () => true,
    },
    {
      id: "open-plugins",
      name: "Open Plugins",
      aliases: ["plugin picker", "add node"],
      run: () => overlays.open("plugins", "popover"),
      enabled: () => true,
    },
    {
      // ADR-020 stage 20.5: the quick switcher - see QuickSwitcherDialog.tsx.
      // Same registration shape as "open-global-search" above (ADR-012's own
      // "register every new surface in the palette" rule) - its real
      // trigger is Ctrl+P (chrome/shortcuts.ts), this is the discoverable
      // palette twin, same posture as "open-library"'s own Ctrl+L twin.
      id: "open-quick-switcher",
      name: "Quick Switcher",
      aliases: ["go to graph", "jump to graph", "recent graphs", "switch graph"],
      run: () => overlays.open("quick-switcher", "dialog"),
      enabled: () => true,
    },
    {
      // ADR-020 stage 20.4: search every workspace's graphs and knowledge
      // documents at once - see GlobalSearchDialog.tsx. Same registration
      // shape as "open-library"/"open-plugins" above (ADR-012's own
      // "register every new surface in the palette" rule).
      id: "open-global-search",
      name: "Open Global Search",
      aliases: ["search everywhere", "search all workspaces", "find in graph"],
      run: () => overlays.open("global-search", "dialog"),
      enabled: () => true,
    },
    {
      // R6.1: plain, ungated - creates a blank note near the current
      // viewport center. Same center-of-viewport formula "add-pin" above
      // already uses; "Add System Prompt Note" is a DIFFERENT, existing
      // path (the plugin picker's generic executePlugin("System Prompt", ...)
      // - see backend/plugins.py) and is deliberately not duplicated here.
      id: "add-note",
      name: "Add Note",
      aliases: ["sticky note", "create note", "new note"],
      run: () => {
        const viewport = rf.getViewport();
        const x = (window.innerWidth / 2 - viewport.x) / viewport.zoom;
        const y = (window.innerHeight / 2 - viewport.y) / viewport.zoom;
        store.addNote(x, y);
      },
      enabled: () => true,
    },
    {
      // R7.5a: real, ungated - store.newChat() already exists (wired from
      // the chat-library dialog); this just gives the palette its own entry
      // point to the same intent, same posture as "add-note" above.
      id: "new-chat",
      name: "New Chat",
      aliases: ["new session", "clear canvas", "start over"],
      run: () => requestNewChat(store),
      enabled: () => true,
    },
    {
      // R7.5c: Save had a real app-bar button and a real store method since
      // R6.5 but no palette entry - a genuine gap this increment closes,
      // since Ctrl+S now needs a palette twin to stay discoverable.
      id: "save-chat",
      name: "Save Chat",
      aliases: ["save", "save session", "persist chat"],
      run: () => store.saveChat(),
      enabled: () => true,
    },
    {
      // R7.5a: zero new capability - rf.fitView with a nodes filter is the
      // same primitive "fit-all" above already uses, just scoped to the
      // current selection instead of every node.
      id: "focus-selection",
      name: "Focus on Selection",
      aliases: ["zoom to selection", "frame selection view"],
      run: () => rf.fitView({ nodes: selectedNodeIds().map((id) => ({ id })), duration: 200 }),
      enabled: hasSelection,
    },
    {
      id: "create-frame",
      name: "Create Frame",
      aliases: ["group into frame", "frame selection"],
      run: () => store.createFrame(selectedNodeIds()),
      enabled: hasMultiSelection,
    },
    {
      id: "create-container",
      name: "Create Container",
      aliases: ["group into container", "container selection"],
      run: () => store.createContainer(selectedNodeIds()),
      enabled: hasMultiSelection,
    },
    {
      // R7.5e: legacy's "Collapse All Nodes" (graphlink_window_navigation.py:12),
      // alias "fold all" - enabled only when at least one chat/conversation/
      // html node exists, same posture as create-frame/create-container's own
      // content-shaped gate above.
      id: "collapse-all-nodes",
      name: "Collapse All Nodes",
      aliases: ["fold all"],
      run: () => store.collapseAllNodes(),
      enabled: hasCollapsibleNodes,
    },
    {
      // R7.5e: legacy's "Expand All Nodes" (graphlink_window_navigation.py:13),
      // alias "unfold all".
      id: "expand-all-nodes",
      name: "Expand All Nodes",
      aliases: ["unfold all"],
      run: () => store.expandAllNodes(),
      enabled: hasCollapsibleNodes,
    },
  ];
}
