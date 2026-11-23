import { Tooltip } from "../../Tooltip";
import { Button } from "../../Button";
import type { GallerySection } from "../Gallery";

function Tooltips() {
  return <div className="gl-gallery-control-panel">
    <h3>Hover delay, focus and shortcuts</h3>
    <div className="gl-gallery-control-row">
      <Tooltip label="Opens the navigation panel" shortcut="Ctrl+K">
        <button type="button" className="gl-button gl-button--secondary">Navigation</button>
      </Tooltip>
      <Tooltip label="Shows additional information">
        <button type="button" className="gl-button gl-button--secondary">Inspect</button>
      </Tooltip>
      <Button disabled disabledReason="Requires an active workspace">Export</Button>
    </div>
  </div>;
}

const section: GallerySection = { title: "Tooltip", order: 40, Component: Tooltips };
export default section;
