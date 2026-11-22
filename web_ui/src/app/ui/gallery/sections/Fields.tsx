import { Field, FormRow, FieldGroup } from "../../Field";
import type { GallerySection } from "../Gallery";
import "../../text-field.css";

function Fields() {
  return (
    <div className="gl-gallery-control-panel">
      <h3>Fields and groups</h3>
      <FieldGroup title="Connection" description="Controls are aligned in a 200 px label column.">
        <FormRow label="Host" layout="inline" description="The address of the server">
          {(control) => <input {...control} className="gl-text-field" defaultValue="localhost" />}
        </FormRow>
        <FormRow label="Port" layout="inline" error="Port is required">
          {(control) => <input {...control} className="gl-text-field" placeholder="Enter a port" />}
        </FormRow>
        <Field label="Workspace" description="Stacked labels work in dialogs">
          <input className="gl-text-field" defaultValue="Graphlink" />
        </Field>
      </FieldGroup>
    </div>
  );
}
const section: GallerySection = { title: "Fields and form rows", order: 35, Component: Fields };
export default section;
