import { Button } from "../../Button";
import { EmptyState } from "../../EmptyState";
import type { GallerySection } from "../Gallery";

function EmptyStates() {
  return <div className="gl-gallery-controls">
    <div className="gl-gallery-control-panel">
      <EmptyState icon="search" title="No results" body="Try a different query."
        actions={<Button variant="primary">Search again</Button>} />
    </div>
    <div className="gl-gallery-control-panel">
      <EmptyState size="lg" icon="file" title="No documents yet"
        body="Add a document to give your workspace context."
        actions={<Button variant="primary">Add document</Button>} />
    </div>
  </div>;
}

const section: GallerySection = { title: "EmptyState", order: 44, Component: EmptyStates };
export default section;
