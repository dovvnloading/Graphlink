import { Button } from "../../Button";
import { Card } from "../../Card";
import type { GallerySection } from "../Gallery";

function Cards() {
  return <div className="gl-gallery-controls">
    <Card title="Workspace import" status="Running" actions={<Button size="sm">View log</Button>}>
      Your files are being prepared.
    </Card>
    <Card title="Last run" status="Done" actions={<Button size="sm" variant="ghost">Open result</Button>}>
      Everything is up to date.
    </Card>
    <Card>Cards can also be used without a header.</Card>
  </div>;
}

const section: GallerySection = { title: "Card", order: 48, Component: Cards };
export default section;
