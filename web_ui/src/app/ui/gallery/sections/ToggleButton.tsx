import { useState } from "react";
import { ToggleButton } from "../../ToggleButton";
import type { GallerySection } from "../Gallery";

function Toggles() {
  const [pinned, setPinned] = useState(true);
  return (
    <div className="gl-gallery-controls">
      <div className="gl-gallery-control-panel">
        <h3>Pressed and unpressed</h3>
        <div className="gl-gallery-control-row">
          <ToggleButton icon="pin" label="Pin view" pressed={pinned} onPressedChange={setPinned} />
          <ToggleButton icon="star" label="Mark favourite" defaultPressed />
          <ToggleButton icon="eye" aria-label="Show details" size="sm" />
          <ToggleButton icon="filter" label="Show filters" disabled />
        </div>
      </div>
    </div>
  );
}

const section: GallerySection = { title: "ToggleButton", order: 32, Component: Toggles };
export default section;
