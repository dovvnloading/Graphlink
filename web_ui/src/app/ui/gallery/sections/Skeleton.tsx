import { Skeleton } from "../../Skeleton";
import type { GallerySection } from "../Gallery";

function Skeletons() {
  return <div className="gl-gallery-control-panel">
    <h3>Loading lines and blocks</h3>
    <Skeleton lines={3} label="Loading list" />
    <Skeleton variant="block" label="Loading preview" height="80px" />
    <p>The shimmer stops when reduced motion is requested.</p>
  </div>;
}

const section: GallerySection = { title: "Skeleton", order: 47, Component: Skeletons };
export default section;
