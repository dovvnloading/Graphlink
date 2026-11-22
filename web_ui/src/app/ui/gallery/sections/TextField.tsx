import { useState } from "react";
import { TextField } from "../../TextField";
import type { GallerySection } from "../Gallery";

function TextFields() {
  const [query, setQuery] = useState("graphlink");
  return (
    <div className="gl-gallery-control-panel">
      <h3>Text fields and suffix actions</h3>
      <div className="gl-gallery-field-grid">
        <TextField label="Search" type="search" value={query} onValueChange={setQuery}
          prefixIcon="search" suffix={{type:"clear"}} description="Clear with the button" />
        <TextField label="API token" type="password" defaultValue="example-token"
          mono suffix={{type:"password"}} description="Use the eye to reveal the value" />
        <TextField label="Timeout" defaultValue="30" suffix={{type:"unit", text:"s"}} size="sm" />
        <TextField label="Email" type="email" placeholder="name@example.com" />
        <TextField label="Project URL" type="url" defaultValue="https://example.com" mono />
        <TextField label="Invalid input" error="Enter a valid URL" defaultValue="not a URL" />
        <TextField label="Disabled input" defaultValue="Read only" disabled />
      </div>
    </div>
  );
}
const section: GallerySection = { title: "Text fields", order: 31, Component: TextFields };
export default section;
