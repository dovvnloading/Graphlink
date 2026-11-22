import { useState } from "react";
import { NumberField } from "../../NumberField";
import type { GallerySection } from "../Gallery";

function NumberFields() {
  const [delay, setDelay] = useState<number | "">(2);
  return (
    <div className="gl-gallery-control-panel">
      <h3>Number steppers</h3>
      <div className="gl-gallery-field-grid">
        <NumberField label="Retry count" defaultValue={2} min={0} max={10} />
        <NumberField label="Timeout" value={delay} onValueChange={setDelay} min={0} max={30} step={0.5} unit="s" description="Arrow keys step; Page Up steps ten times" />
        <NumberField label="Invalid value" error="Adjust this setting" defaultValue={3} />
        <NumberField label="Disabled number" defaultValue={5} disabled />
      </div>
    </div>
  );
}
const section: GallerySection = { title: "Number fields", order: 33, Component: NumberFields };
export default section;
