import { useState } from "react";
import { Dialog, OverlayProvider, Popover, useOverlays } from "../../../overlays/overlays";
import { Select } from "../../Select";
import type { GallerySection } from "../Gallery";

function OverlayExamples() {
  const overlays = useOverlays();
  const [choice, setChoice] = useState("medium");
  return (
    <div className="gl-gallery-control-panel">
      <h3>Overlay stack and dialog anatomy</h3>
      <p>One top-level surface; a dialog can host a Select listbox or a confirmation above it.</p>
      <div className="gl-gallery-control-row">
        <button type="button" className="settings-button" onClick={() => overlays.open("gallery-dialog", "dialog")}>
          Open dialog
        </button>
        <button type="button" className="settings-button" data-overlay-trigger="gallery-popover"
          onClick={() => overlays.toggle("gallery-popover", "popover")}>
          Open popover
        </button>
        <Popover name="gallery-popover" label="Example popover" anchored>
          <p>A light surface with anchored placement and motion.</p>
          <button className="settings-button" type="button" onClick={overlays.close}>Dismiss</button>
        </Popover>
      </div>
      <Dialog name="gallery-dialog" title="New workspace" description="Choose your preferences."
        size="md" status="Changes are local"
        footer={<>
          <button type="button" className="settings-button" onClick={overlays.close}>Cancel</button>
          <button type="button" className="settings-button settings-button-primary"
            onClick={() => overlays.closeLayer("gallery-dialog")}>Save workspace</button>
        </>}>
        <label className="settings-field">
          <span className="settings-field-label">Workspace name</span>
          <input className="settings-select" placeholder="New workspace" data-autofocus />
        </label>
        <Select label="Review level" value={choice} onChange={setChoice}
          options={[
            { value: "light", label: "Light" },
            { value: "medium", label: "Medium", description: "Ask for confirmation on important changes" },
            { value: "strict", label: "Strict" },
          ]} />
        <button type="button" className="settings-button"
          onClick={() => overlays.openNested("gallery-confirm", "dialog")}>Show nested confirmation</button>
        <Dialog name="gallery-confirm" title="Confirm changes" size="sm"
          footer={<>
            <button type="button" className="settings-button" data-autofocus onClick={overlays.close}>Cancel</button>
            <button type="button" className="settings-button settings-button-primary"
              onClick={() => overlays.closeLayer("gallery-confirm")}>Confirm</button>
          </>}>
          <p>This confirmation stays above its parent dialog until dismissed.</p>
        </Dialog>
      </Dialog>
    </div>
  );
}

function Overlays() {
  return <OverlayProvider><OverlayExamples /></OverlayProvider>;
}

const section: GallerySection = { title: "Overlays", order: 30, Component: Overlays };
export default section;
