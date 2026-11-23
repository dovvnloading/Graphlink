import { ProgressBar } from "../../ProgressBar";
import type { GallerySection } from "../Gallery";

function ProgressBars() {
  return <div className="gl-gallery-control-panel">
    <h3>Determinate and indeterminate</h3>
    <ProgressBar label="Files imported" value={3} max={8} />
    <ProgressBar label="Preparing workspace" indeterminate />
  </div>;
}

const section: GallerySection = { title: "ProgressBar", order: 46, Component: ProgressBars };
export default section;
