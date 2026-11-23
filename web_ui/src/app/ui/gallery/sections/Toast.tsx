import { Button } from "../../Button";
import { Toast, Toaster, toast } from "../../Toast";
import type { GallerySection } from "../Gallery";

function Toasts() {
  return <div className="gl-gallery-controls">
    <div className="gl-gallery-control-panel">
      <h3>Persistent variants and detail disclosure</h3>
      <Toast title="Check settings before retrying." variant="warning" />
      <Toast title="Couldn't export. Review the log and retry." variant="error"
        details="The export destination is unavailable."
        action={{ label: "Try again", onClick: () => toast.show({ title: "Retry requested", variant: "info" }) }} />
    </div>
    <div className="gl-gallery-control-panel">
      <h3>Live stacking and auto-dismiss</h3>
      <div className="gl-gallery-control-row">
        <Button onClick={() => toast.show({ title: "Changes saved", variant: "success" })}>Show success</Button>
        <Button onClick={() => toast.show({ title: "Couldn't sync. Check the connection.", variant: "error" })}>
          Show error
        </Button>
        <Button onClick={() => toast.show({ title: "Still processing", variant: "warning" })}>Show warning</Button>
      </div>
      <Toaster />
    </div>
  </div>;
}

const section: GallerySection = { title: "Toast and Toaster", order: 41, Component: Toasts };
export default section;
