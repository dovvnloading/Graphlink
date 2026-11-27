import { useReactFlow } from "@xyflow/react";
import { lazy, Suspense, useRef, useState, useSyncExternalStore } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import { downloadBlob } from "../canvas/downloadTextFile";
import { useOverlays } from "../overlays/overlays";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";
import { MenuButton, MenuItem } from "../ui/Menu";
import { toast } from "../ui/Toast";
import type { MenuPosition } from "../canvas/menuPosition";
import { openHelp } from "./helpNavigation";
import "./AppBar.css";

const LazyAppBarOverflowMenu = lazy(() =>
  import("./AppBarOverflowMenu").then((module) => ({ default: module.AppBarOverflowMenu })),
);

/** Desktop toolbar; only the two session commands and Settings survive the single spill guard. */
export function AppBar({ store }: { store: SceneStore }) {
  const overlays = useOverlays();
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const { setViewport, getViewport, getNodes } = useReactFlow();
  const overflowTrigger = useRef<HTMLButtonElement | null>(null);
  const [overflowPosition, setOverflowPosition] = useState<MenuPosition | null>(null);
  // Shared by the toolbar and overflow entry points: one capture at a time,
  // since export moves the live viewport and suspends virtualization (#1130).
  const pngExportingRef = useRef(false);
  const [pngExporting, setPngExporting] = useState(false);
  const canvasEmpty = scene.nodes.length === 0;

  const exportMethods = async () => {
    const markdown = await store.exportWorkspaceMethods();
    downloadBlob(new Blob([markdown], { type: "text/markdown;charset=utf-8" }),
      "graphlink-workspace-methods.md");
  };
  const exportPng = async () => {
    if (pngExportingRef.current) return;
    pngExportingRef.current = true;
    setPngExporting(true);
    const progress = toast.show({ title: "Exporting PNG…", variant: "info" });
    try {
      const { exportCanvasAsPng } = await import("../canvas/exportCanvasPng");
      const outcome = await exportCanvasAsPng(
        { getNodes, getViewport, setViewport },
        "--gl-graph-canvas",
        (value) => store.setExportInProgress(value),
      );
      toast.dismiss(progress);
      if (outcome === "exported") toast.show({ title: "PNG export downloaded.", variant: "success" });
      else if (outcome === "empty") {
        toast.show({ title: "Nothing to export. Add a node to the canvas, then export again.", variant: "info" });
      } else if (outcome === "no-canvas") {
        toast.show({ title: "Couldn't find the canvas to export. Close any open view, then try again.", variant: "error" });
      } else {
        toast.show({
          title: "Couldn't export the PNG. An image on the canvas may be unreachable. Check graphlink.log, then try again.",
          variant: "error",
        });
      }
    } catch (error) {
      console.error("[export-png]", error);
      toast.dismiss(progress);
      toast.show({ title: "Couldn't load the PNG exporter. Reload Graphlink, then try again.", variant: "error" });
    } finally {
      pngExportingRef.current = false;
      setPngExporting(false);
    }
  };
  const pngDisabledReason = pngExporting ? "An export is already running" : canvasEmpty ? "Add a node to export a PNG" : undefined;
  // Verified-result availability is not present in the client scene contract.
  // Leave methods export enabled; the server checks receipts and its precise
  // error reply is displayed once by the transport's intent-error listener.
  const requestMethodsExport = () => {
    void exportMethods().catch(() => { /* Error reply is toasted centrally. */ });
  };
  const requestPngExport = () => { void exportPng(); };
  const toggleOverflow = (button: HTMLButtonElement) => {
    if (overflowPosition) {
      setOverflowPosition(null);
      return;
    }
    const rect = button.getBoundingClientRect();
    if (rect) setOverflowPosition({ x: rect.left, y: rect.bottom + 6 });
  };

  return (
    <div className="appbar" role="toolbar" aria-label="Application bar">
      <div className="appbar-group">
        <Button variant="ghost" size="sm" data-overlay-trigger="library"
          aria-pressed={overlays.isOpen("library")}
          onClick={() => overlays.toggle("library", "dialog")}>Library</Button>
        <Button variant="ghost" size="sm" data-overlay-trigger="workflow-library" aria-pressed={overlays.isOpen("workflow-library")}
          onClick={() => overlays.toggle("workflow-library", "dialog")}>Workflows</Button>
        <Button variant="ghost" size="sm" onClick={() => store.saveChat()}>Save</Button>
      </div>

      <div className="appbar-group appbar-tier">
        <IconButton icon="undo" label="Undo" shortcut="Ctrl+Z" size="md"
          disabled={!scene.canUndo} onClick={() => store.undo()} />
        <IconButton icon="redo" label="Redo" shortcut="Ctrl+Shift+Z" size="md"
          disabled={!scene.canRedo} onClick={() => store.redo()} />
      </div>

      <div className="appbar-group appbar-tier">
        <IconButton icon="organize" label="Organize" size="md"
          onClick={() => overlays.open("organize", "dialog")} />
        <IconButton icon="pin" label="Pins" size="md" data-overlay-trigger="pins"
          aria-pressed={overlays.isOpen("pins")}
          onClick={() => overlays.toggle("pins", "popover")} />
        <MenuButton label="Export" className="appbar-menu-button" menuLabel="Export">
          <MenuItem label="Export PNG…" icon="download" onSelect={requestPngExport}
            disabled={pngDisabledReason !== undefined} disabledReason={pngDisabledReason} />
          <MenuItem label="Export methods…" icon="document-text" onSelect={requestMethodsExport} />
        </MenuButton>
      </div>

      <div className="appbar-group appbar-tier">
        <Button variant="ghost" size="sm" iconEnd="chevron-down"
          data-overlay-trigger="view" aria-expanded={overlays.isOpen("view")}
          aria-pressed={overlays.isOpen("view")}
          onClick={() => overlays.toggle("view", "popover")}>View</Button>
        <Button variant="ghost" size="sm" iconEnd="chevron-down"
          data-overlay-trigger="plugins" aria-expanded={overlays.isOpen("plugins")}
          aria-pressed={overlays.isOpen("plugins")}
          onClick={() => overlays.toggle("plugins", "popover")}>Plugins</Button>
      </div>

      <span className="appbar-spacer" aria-hidden="true" />

      <IconButton icon="more-horizontal" label="More toolbar actions"
        size="md" className="appbar-overflow-trigger" aria-haspopup="menu"
        aria-expanded={overflowPosition !== null}
        onClick={(event) => {
          overflowTrigger.current = event.currentTarget;
          toggleOverflow(event.currentTarget);
        }} />
      {overflowPosition && (
        <Suspense fallback={<span role="status">Loading toolbar actions…</span>}>
          <LazyAppBarOverflowMenu store={store} position={overflowPosition}
            onClose={() => setOverflowPosition(null)} ignoreRef={overflowTrigger}
            exportPng={exportPng} exportMethods={exportMethods} pngDisabledReason={pngDisabledReason} />
        </Suspense>
      )}

      <div className="appbar-group appbar-tier">
        <IconButton icon="search" label="Global Search" size="md"
          data-overlay-trigger="global-search" aria-pressed={overlays.isOpen("global-search")}
          onClick={() => overlays.toggle("global-search", "dialog")} />
        <IconButton icon="knowledge" label="Knowledge" size="md"
          data-overlay-trigger="knowledge" aria-pressed={overlays.isOpen("knowledge")}
          onClick={() => overlays.toggle("knowledge", "dialog")} />
        <IconButton icon="builder" label="Builder" size="md"
          data-overlay-trigger="builder-launch" aria-pressed={overlays.isOpen("builder-launch")}
          onClick={() => overlays.toggle("builder-launch", "dialog")} />
        <IconButton icon="agent" label="Agent" size="md"
          data-overlay-trigger="harness-launch" aria-pressed={overlays.isOpen("harness-launch")}
          onClick={() => overlays.toggle("harness-launch", "dialog")} />
      </div>

      <div className="appbar-group appbar-tier">
        <MenuButton label="Help" aria-label="Help and diagnostics"
          menuLabel="Help and diagnostics" className="appbar-menu-button">
          <MenuItem label="Help" icon="help"
            onSelect={() => openHelp(overlays, "")} />
          <MenuItem label="Keyboard shortcuts" icon="keyboard"
            onSelect={() => openHelp(overlays, "Keyboard")} />
          <MenuItem label="Diagnostics" icon="diagnostics"
            onSelect={() => overlays.open("diagnostics", "dialog")} />
          <MenuItem label="About" icon="info"
            onSelect={() => overlays.open("about", "dialog")} />
        </MenuButton>
      </div>

      <div className="appbar-group">
        <IconButton icon="settings" label="Settings" size="md"
          data-overlay-trigger="settings" aria-pressed={overlays.isOpen("settings")}
          onClick={() => overlays.toggle("settings", "dialog")} />
      </div>
    </div>
  );
}
