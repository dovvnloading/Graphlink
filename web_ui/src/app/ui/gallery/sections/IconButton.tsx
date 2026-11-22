import { IconButton } from "../../IconButton";
import type { GallerySection } from "../Gallery";

function IconButtons() {
  return (
    <div className="gl-gallery-controls">
      <div className="gl-gallery-control-panel">
        <h3>Variants and sizes</h3>
        <div className="gl-gallery-control-row">
          <IconButton icon="search" label="Search" shortcut="Ctrl+K" size="sm" />
          <IconButton icon="copy" label="Copy link" variant="secondary" />
          <IconButton icon="download" label="Download" variant="primary" size="lg" />
          <IconButton icon="close" label="Close" disabled />
        </div>
      </div>
    </div>
  );
}

const section: GallerySection = { title: "IconButton", order: 31, Component: IconButtons };
export default section;
