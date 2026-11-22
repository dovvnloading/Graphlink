import { Spinner } from "../../Spinner";
import type { GallerySection } from "../Gallery";

function Spinners() {
  return (
    <div className="gl-gallery-controls">
      <div className="gl-gallery-control-panel">
        <h3>Animated and reduced-motion states</h3>
        <div className="gl-gallery-control-row">
          <Spinner size={12} label="Running" />
          <Spinner size={14} label="Running" />
          <Spinner size={16} label="Running" />
          <Spinner size={20} label="Running" />
        </div>
        <p>Reduced-motion preferences replace the rotating arc with the static running glyph.</p>
      </div>
    </div>
  );
}

const section: GallerySection = { title: "Spinner", order: 35, Component: Spinners };
export default section;
