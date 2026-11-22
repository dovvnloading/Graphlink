import { Disclosure } from "../../Disclosure";
import type { GallerySection } from "../Gallery";

function Disclosures() {
  return (
    <div className="gl-gallery-control-panel">
      <h3>Inline and section disclosures</h3>
      <Disclosure title="Advanced options">
        <p>Extra controls appear here when expanded.</p>
      </Disclosure>
      <Disclosure title="Connection details" variant="section" defaultOpen>
        <p>Connection details are visible by default.</p>
      </Disclosure>
      <Disclosure title="Locked section" variant="section" disabled>Hidden content</Disclosure>
    </div>
  );
}
const section: GallerySection = { title: "Disclosures", order: 36, Component: Disclosures };
export default section;
