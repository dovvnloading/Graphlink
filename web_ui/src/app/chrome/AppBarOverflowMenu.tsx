// Only the desktop spill list is deferred. Menu is portaled so it remains
// keyboard-accessible even when the appbar's container query hides the groups.
import { useSyncExternalStore, type RefObject } from "react";
import type { SceneStore } from "../canvas/sceneStore";
import type { MenuPosition } from "../canvas/menuPosition";
import { useOverlays } from "../overlays/overlays";
import { Menu, MenuGroup, MenuItem } from "../ui/Menu";

export function AppBarOverflowMenu({
  store, exportPng, exportMethods, position, onClose, ignoreRef,
}: {
  store: SceneStore;
  exportPng: () => Promise<void>;
  exportMethods: () => Promise<void>;
  position: MenuPosition;
  onClose: () => void;
  ignoreRef: RefObject<HTMLElement | null>;
}) {
  const overlays = useOverlays();
  const scene = useSyncExternalStore(store.subscribe, store.getScene);
  const downloadPng = () => { void exportPng().catch((error: unknown) => console.error("[export-png]", error)); };
  const downloadMethods = () => {
    // The transport listener owns server failures; do not add a second inline alert.
    void exportMethods().catch(() => {});
  };

  return (
    <Menu position={position} onClose={onClose} ignoreRef={ignoreRef}
      ariaLabel="More toolbar actions" className="appbar-overflow-menu">
      <MenuGroup label="Edit">
        <MenuItem label="Undo" icon="undo" shortcut="Ctrl+Z"
          disabled={!scene.canUndo} onSelect={() => store.undo()} />
        <MenuItem label="Redo" icon="redo" shortcut="Ctrl+Shift+Z"
          disabled={!scene.canRedo} onSelect={() => store.redo()} />
      </MenuGroup>
      <MenuGroup label="Arrange">
        <MenuItem label="Organize" icon="organize"
          onSelect={() => overlays.open("organize", "dialog")} />
        <MenuItem label="Pins" icon="pin"
          onSelect={() => overlays.toggle("pins", "popover")} />
        <MenuItem label="Export PNG…" icon="download" onSelect={downloadPng} />
        <MenuItem label="Export methods…" icon="document-text" onSelect={downloadMethods} />
      </MenuGroup>
      <MenuGroup label="Panels">
        <MenuItem label="View" icon="eye"
          onSelect={() => overlays.toggle("view", "popover")} />
        <MenuItem label="Plugins" icon="puzzle"
          onSelect={() => overlays.toggle("plugins", "popover")} />
      </MenuGroup>
      <MenuGroup label="Workspace">
        <MenuItem label="Global Search" icon="search" shortcut="Ctrl+F"
          onSelect={() => overlays.toggle("global-search", "dialog")} />
        <MenuItem label="Knowledge" icon="knowledge"
          onSelect={() => overlays.toggle("knowledge", "dialog")} />
        <MenuItem label="Builder" icon="builder"
          onSelect={() => overlays.toggle("builder-launch", "dialog")} />
        <MenuItem label="Agent" icon="agent"
          onSelect={() => overlays.toggle("harness-launch", "dialog")} />
        <MenuItem label="Help" icon="help"
          onSelect={() => overlays.open("help", "dialog")} />
        <MenuItem label="Keyboard shortcuts" icon="keyboard"
          onSelect={() => overlays.open("help", "dialog")} />
        <MenuItem label="Diagnostics" icon="diagnostics"
          onSelect={() => overlays.open("diagnostics", "dialog")} />
        <MenuItem label="About" icon="info"
          onSelect={() => overlays.open("about", "dialog")} />
      </MenuGroup>
    </Menu>
  );
}
