import { Button } from "../../Button";
import { ConfirmDialog, useConfirm } from "../../ConfirmDialog";
import { OverlayProvider } from "../../../overlays/overlays";
import type { GallerySection } from "../Gallery";

function ConfirmationExample() {
  const confirm = useConfirm();
  return <div className="gl-gallery-control-panel">
    <h3>Primary action and destructive focus</h3>
    <div className="gl-gallery-control-row">
      <Button onClick={() => { void confirm({
        title: "Publish workspace?", body: "This makes the workspace available to your team.",
        confirmLabel: "Publish workspace",
      }); }}>Publish workspace…</Button>
      <Button destructive onClick={() => { void confirm({
        title: "Delete workspace?", body: "This cannot be undone.",
        confirmLabel: "Delete workspace", destructive: true,
      }); }}>Delete workspace…</Button>
    </div>
    <ConfirmDialog />
  </div>;
}

function Confirmations() {
  return <OverlayProvider><ConfirmationExample /></OverlayProvider>;
}

const section: GallerySection = { title: "ConfirmDialog", order: 42, Component: Confirmations };
export default section;
