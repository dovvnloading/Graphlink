// Only the desktop overflow action list is deferred. Inline toolbar
// buttons stay immediately responsive and keep their full keyboard behavior.
import { useState, useSyncExternalStore } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import { Popover, useOverlays } from "../overlays/overlays";

export function AppBarOverflowMenu({ store, exportPng, exportMethods }: {
  store: SceneStore;
  exportPng: () => Promise<void>;
  exportMethods: () => Promise<void>;
}) {
  const overlays = useOverlays();
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const [methodsExporting, setMethodsExporting] = useState(false);
  const [methodsError, setMethodsError] = useState("");
  const closeMenu = () => overlays.close();
  const menuItem = (surface: string) =>
    "appbar-menu-item" + (overlays.isOpen(surface) ? " checked" : "");
  const overflowItem = (surface: string) => `${menuItem(surface)} appbar-overflow-item`;

  return (
      <Popover
        name="toolbar-overflow"
        label="More toolbar actions"
        className="appbar-menu appbar-overflow-menu"
      >
        <p className="appbar-menu-heading">
          Edit
        </p>
        <button
          type="button"
          className="appbar-menu-item appbar-overflow-item"
          onClick={() => {
            store.undo();
            closeMenu();
          }}
          disabled={!scene.canUndo}
        >
          Undo
        </button>
        <button
          type="button"
          className="appbar-menu-item appbar-overflow-item"
          onClick={() => {
            store.redo();
            closeMenu();
          }}
          disabled={!scene.canRedo}
        >
          Redo
        </button>
        <p className="appbar-menu-heading">
          Arrange
        </p>
        <button
          type="button"
          className="appbar-menu-item appbar-overflow-item"
          onClick={() => overlays.open("organize", "dialog")}
        >
          Organize
        </button>
        <button
          type="button"
          className={overflowItem("pins")}
          aria-pressed={overlays.isOpen("pins")}
          onClick={() => overlays.toggle("pins", "popover")}
        >
          Pins
        </button>
        <button
          type="button"
          className="appbar-menu-item appbar-overflow-item"
          onClick={() => {
            exportPng();
            closeMenu();
          }}
        >
          Export PNG
        </button>

        <p className="appbar-menu-heading">
          Panels
        </p>
        <button
          type="button"
          className={overflowItem("view")}
          aria-pressed={overlays.isOpen("view")}
          onClick={() => overlays.toggle("view", "popover")}
        >
          View
        </button>
        <button
          type="button"
          className={overflowItem("plugins")}
          aria-pressed={overlays.isOpen("plugins")}
          onClick={() => overlays.toggle("plugins", "popover")}
        >
          Plugins
        </button>

        <p className="appbar-menu-heading">
          Workspace
        </p>
        <button
          type="button"
          className="appbar-menu-item appbar-overflow-item"
          disabled={methodsExporting}
          onClick={async () => {
            setMethodsError("");
            setMethodsExporting(true);
            try {
              await exportMethods();
              closeMenu();
            } catch (error) {
              setMethodsError(error instanceof Error ? error.message : "Methods export failed.");
            } finally {
              setMethodsExporting(false);
            }
          }}
        >
          {methodsExporting ? "Exporting methods…" : "Export methods"}
        </button>
        {methodsError && <p role="alert">{methodsError}</p>}
        <button
          type="button"
          className={overflowItem("global-search")}
          aria-pressed={overlays.isOpen("global-search")}
          onClick={() => overlays.toggle("global-search", "dialog")}
        >
          Global Search
        </button>
        <button
          type="button"
          className={overflowItem("knowledge")}
          aria-pressed={overlays.isOpen("knowledge")}
          onClick={() => overlays.toggle("knowledge", "dialog")}
        >
          Knowledge
        </button>
        <button
          type="button"
          className={overflowItem("builder-launch")}
          aria-pressed={overlays.isOpen("builder-launch")}
          onClick={() => overlays.toggle("builder-launch", "dialog")}
        >
          Builder
        </button>
        <button
          type="button"
          className={overflowItem("harness-launch")}
          aria-pressed={overlays.isOpen("harness-launch")}
          onClick={() => overlays.toggle("harness-launch", "dialog")}
        >
          Agent
        </button>
        <button
          type="button"
          className={overflowItem("diagnostics")}
          aria-pressed={overlays.isOpen("diagnostics")}
          onClick={() => overlays.toggle("diagnostics", "dialog")}
        >
          Diagnostics
        </button>
        <button
          type="button"
          className={overflowItem("help")}
          aria-pressed={overlays.isOpen("help")}
          onClick={() => overlays.toggle("help", "dialog")}
        >
          Help
        </button>
        <button
          type="button"
          className={overflowItem("about")}
          aria-pressed={overlays.isOpen("about")}
          onClick={() => overlays.toggle("about", "dialog")}
        >
          About
        </button>
      </Popover>
  );
}
