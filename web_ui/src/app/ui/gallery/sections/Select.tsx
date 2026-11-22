import { useState } from "react";
import { OverlayProvider } from "../../../overlays/overlays";
import { Select } from "../../Select";
import type { GallerySection } from "../Gallery";

const OPTIONS = [
  { value: "alpha", label: "Alpha", group: "Letters" },
  { value: "beta", label: "Beta", group: "Letters", description: "Recommended" },
  { value: "gamma", label: "Gamma", group: "Letters", disabled: true },
  { value: "delta", label: "Delta", group: "Letters" },
  { value: "echo", label: "Echo", group: "Letters" },
  { value: "foxtrot", label: "Foxtrot", group: "More" },
  { value: "golf", label: "Golf", group: "More" },
  { value: "hotel", label: "Hotel", group: "More" },
  { value: "india", label: "India", group: "More" },
] as const;

function SelectExamples() {
  const [simple, setSimple] = useState("beta");
  const [many, setMany] = useState("alpha");
  return (
    <div className="gl-gallery-control-panel">
      <h3>Kit Select</h3>
      <p>Accessible listbox, keyboard navigation and search above eight options.</p>
      <div className="gl-gallery-field-grid">
        <Select label="Priority" value={simple} onChange={setSimple}
          options={[{ value: "alpha", label: "High" }, { value: "beta", label: "Normal" },
            { value: "gamma", label: "Low" }]} />
        <Select label="Large catalogue" value={many} onChange={setMany} options={OPTIONS.map((item) => ({ ...item }))} />
        <Select label="Unavailable select" value="alpha" onChange={() => {}} disabled options={OPTIONS.map((item) => ({ ...item }))} />
      </div>
    </div>
  );
}

function SelectGallery() {
  return <OverlayProvider><SelectExamples /></OverlayProvider>;
}
const section: GallerySection = { title: "Select", order: 31, Component: SelectGallery };
export default section;
