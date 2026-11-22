import { Kbd } from "../../Kbd";
import type { GallerySection } from "../Gallery";

function Shortcuts() {
  return (
    <div className="gl-gallery-controls">
      <div className="gl-gallery-control-panel">
        <h3>Shortcut caps</h3>
        <div className="gl-gallery-control-row">
          <Kbd shortcut="Ctrl+K" />
          <Kbd shortcut="Ctrl+Shift+K" />
          <Kbd shortcut="Esc" />
        </div>
      </div>
    </div>
  );
}

const section: GallerySection = { title: "Kbd", order: 36, Component: Shortcuts };
export default section;
