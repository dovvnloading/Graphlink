import { TextArea } from "../../TextArea";
import type { GallerySection } from "../Gallery";

function TextAreas() {
  return (
    <div className="gl-gallery-control-panel">
      <h3>Growing text areas</h3>
      <div className="gl-gallery-field-grid">
        <TextArea label="Description" minRows={2} maxRows={5} description="Grows as you type" defaultValue="Write a description." />
        <TextArea label="Prompt" minRows={3} maxRows={6} placeholder="Enter the prompt…" />
        <TextArea label="Notes with an error" error="Notes cannot be empty" />
        <TextArea label="Archived notes" disabled defaultValue="This record cannot be edited." />
      </div>
    </div>
  );
}
const section: GallerySection = { title: "Text areas", order: 32, Component: TextAreas };
export default section;
