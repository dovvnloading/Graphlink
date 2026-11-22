import { useState } from "react";
import { Checkbox } from "../../Checkbox";
import { RadioGroup } from "../../RadioGroup";
import { Switch } from "../../Switch";
import type { GallerySection } from "../Gallery";

function CheckboxRadioSwitch() {
  const [checked, setChecked] = useState(true);
  const [enabled, setEnabled] = useState(false);
  const [selected, setSelected] = useState("local");
  return (
    <div className="gl-gallery-controls">
      <div className="gl-gallery-control-panel">
        <h3>Checkboxes</h3>
        <div className="gl-gallery-control-row">
          <Checkbox label="Save to history" checked={checked} onChange={(event) => setChecked(event.target.checked)} description="Include sessions in your library" />
          <Checkbox label="Select all" indeterminate />
          <Checkbox label="Unavailable" disabled defaultChecked />
          <Checkbox label="Consent" error="Consent is required" />
        </div>
      </div>
      <div className="gl-gallery-control-panel">
        <h3>Radio options</h3>
        <RadioGroup label="Result detail" defaultValue="normal" options={[{value:"compact", label:"Compact"}, {value:"normal", label:"Normal"}, {value:"full", label:"Full", disabled:true}]} />
        <RadioGroup label="Provider" variant="card" value={selected} onValueChange={setSelected}
          options={[{value:"local", label:"Local model", description:"Runs on this computer"}, {value:"cloud", label:"Cloud model", description:"Uses an API connection"}]} />
      </div>
      <div className="gl-gallery-control-panel">
        <h3>Switches</h3>
        <div className="gl-gallery-control-row">
          <Switch label="Live updates" checked={enabled} onCheckedChange={setEnabled} description="Update while running" />
          <Switch label="Locked setting" defaultChecked disabled />
        </div>
      </div>
    </div>
  );
}
const section: GallerySection = { title: "Checkboxes, radios and switches", order: 30, Component: CheckboxRadioSwitch };
export default section;
