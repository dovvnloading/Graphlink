import { InlineAlert } from "../../InlineAlert";
import type { GallerySection } from "../Gallery";

function InlineAlerts() {
  return <div className="gl-gallery-controls">
    <InlineAlert title="Information" body="This workspace is available offline." />
    <InlineAlert variant="success" title="Saved" body="Your changes are available." />
    <InlineAlert variant="warning" title="Connection unstable" body="Changes may take longer to sync." />
    <InlineAlert variant="error" title="Couldn't save" body="The connection was lost. Try again."
      action={{ label: "Try again", onClick: () => {} }} />
  </div>;
}

const section: GallerySection = { title: "InlineAlert", order: 43, Component: InlineAlerts };
export default section;
