import { Button } from "../../Button";
import type { GallerySection } from "../Gallery";

function Buttons() {
  return (
    <div className="gl-gallery-controls">
      <div className="gl-gallery-control-panel">
        <h3>Variants and actions</h3>
        <div className="gl-gallery-control-row">
          <Button variant="primary" iconStart="download">Download report</Button>
          <Button variant="secondary" iconStart="copy">Copy link</Button>
          <Button variant="ghost" iconEnd="chevron-right">Open library</Button>
          <Button destructive>Delete chat</Button>
        </div>
      </div>
      <div className="gl-gallery-control-panel">
        <h3>Sizes and states</h3>
        <div className="gl-gallery-control-row">
          <Button size="sm">Save changes</Button>
          <Button size="md">Save changes</Button>
          <Button size="lg">Save changes</Button>
          <Button loading iconStart="download">Download report</Button>
          <Button disabled disabledReason="Requires an open workspace">Export workspace</Button>
        </div>
      </div>
    </div>
  );
}

const section: GallerySection = { title: "Button", order: 30, Component: Buttons };
export default section;
