import type { ReactFlowInstance } from "@xyflow/react";
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

  return [
    {
      id: "fit-all",
      name: "Fit All to View",
      aliases: ["fit screen", "zoom fit"],
      run: () => rf.fitView({ duration: 200 }),
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
  ];
}
