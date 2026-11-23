import { Badge, StatusPill } from "../../Badge";
import type { GallerySection } from "../Gallery";

function Badges() {
  return <div className="gl-gallery-control-panel">
    <h3>Counts, labels and semantic status words</h3>
    <div className="gl-gallery-control-row">
      <Badge>12</Badge><Badge size="xs">Local</Badge>
      {(["Running", "Done", "Failed", "Cancelled", "Waiting for approval", "Out of date", "Skipped"] as const)
        .map((status) => <StatusPill key={status} status={status} />)}
    </div>
  </div>;
}

const section: GallerySection = { title: "Badge and StatusPill", order: 45, Component: Badges };
export default section;
