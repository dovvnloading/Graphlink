import { useState } from "react";
import { Slider } from "../../Slider";
import type { GallerySection } from "../Gallery";

function Sliders() {
  const [value, setValue] = useState(35);
  return (
    <div className="gl-gallery-control-panel">
      <h3>Range controls</h3>
      <div className="gl-gallery-field-grid">
        <Slider label="Volume" value={value} onValueChange={setValue} showValue formatValue={(v) => v + "%"}
          ticks={[{value:0, label:"0%"}, {value:50, label:"50%"}, {value:100, label:"100%"}]} />
        <Slider label="Steps" defaultValue={4} min={0} max={10} step={2} showValue />
        <Slider label="Invalid range" defaultValue={20} error="Increase the threshold" />
        <Slider label="Locked value" defaultValue={50} disabled showValue />
      </div>
    </div>
  );
}
const section: GallerySection = { title: "Sliders", order: 34, Component: Sliders };
export default section;
