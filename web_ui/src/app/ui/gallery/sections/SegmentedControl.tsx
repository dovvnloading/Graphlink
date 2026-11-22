import { useState } from "react";
import { SegmentedControl } from "../../SegmentedControl";
import type { GallerySection } from "../Gallery";

const options = [
  { value: "list", label: "List", icon: "document-text" as const },
  { value: "grid", label: "Grid", icon: "grid-lines" as const },
  { value: "cards", label: "Cards", icon: "layers" as const },
];

function Segments() {
  const [view, setView] = useState("grid");
  return (
    <div className="gl-gallery-controls">
      <div className="gl-gallery-control-panel">
        <h3>Roving radio selection</h3>
        <div className="gl-gallery-control-row">
          <SegmentedControl label="View mode" options={options} value={view} onChange={setView} />
          <SegmentedControl label="Compact view mode" options={options} size="sm" defaultValue="list" />
          <SegmentedControl label="Unavailable view mode" options={options} disabled />
        </div>
      </div>
    </div>
  );
}

const section: GallerySection = { title: "SegmentedControl", order: 33, Component: Segments };
export default section;
