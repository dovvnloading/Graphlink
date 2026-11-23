import { useState } from "react";
import { OverlayProvider } from "../../../overlays/overlays";
import { ContextMenu, MenuButton, MenuGroup, MenuItem, MenuSeparator, SubMenu } from "../../Menu";
import type { GallerySection } from "../Gallery";

function MenuExamples() {
  const [pinned, setPinned] = useState(false);
  const [format, setFormat] = useState<"svg" | "png">("svg");
  const [lastAction, setLastAction] = useState("No action yet");
  return (
    <div className="gl-gallery-controls">
      <div className="gl-gallery-control-panel">
        <h3>Menu button</h3>
        <p>Arrow keys, type-ahead, Enter or Space to activate, and Escape to return to the trigger.</p>
        <div className="gl-gallery-control-row">
          <MenuButton label="File actions">
            <MenuGroup label="File">
              <MenuItem label="Copy link" icon="copy" shortcut="Ctrl+C"
                onSelect={() => setLastAction("Copied link")} />
              <MenuItem label="Save a copy" icon="download" shortcut="Ctrl+S"
                description="Download to this computer" onSelect={() => setLastAction("Saved copy")} />
              <MenuItem label="Unavailable action" disabled disabledReason="Requires an active workspace" />
            </MenuGroup>
            <MenuSeparator />
            <MenuItem label="Pin to workspace" checked={pinned}
              onSelect={() => setPinned((current) => !current)} />
            <SubMenu label="Export as" icon="download">
              <MenuItem label="SVG" kind="radio" checked={format === "svg"}
                onSelect={() => { setFormat("svg"); setLastAction("Export SVG"); }} />
              <MenuItem label="PNG" kind="radio" checked={format === "png"}
                onSelect={() => { setFormat("png"); setLastAction("Export PNG"); }} />
            </SubMenu>
            <MenuItem label="Delete file" destructive onSelect={() => setLastAction("Delete requested")} />
          </MenuButton>
        </div>
      </div>
      <div className="gl-gallery-control-panel">
        <h3>Context menu</h3>
        <p>Right-click the target, or focus it and press Shift+F10 or the ContextMenu key.</p>
        <div className="gl-gallery-control-row">
          <ContextMenu ariaLabel="Item actions" trigger={<button type="button" className="gl-menu-button">Sample node</button>}>
            <MenuItem label="Rename node" icon="edit" onSelect={() => setLastAction("Rename node")} />
            <MenuItem label="Duplicate node" icon="copy" onSelect={() => setLastAction("Duplicate node")} />
            <MenuItem label="Delete node" destructive onSelect={() => setLastAction("Delete node")} />
          </ContextMenu>
        </div>
        <p role="status">{lastAction}</p>
      </div>
    </div>
  );
}

function MenuGallery() {
  return <OverlayProvider><MenuExamples /></OverlayProvider>;
}

const section: GallerySection = { title: "Menus", order: 37, Component: MenuGallery };
export default section;
