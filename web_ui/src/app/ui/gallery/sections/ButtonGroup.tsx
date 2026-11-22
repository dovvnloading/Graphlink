import { Button } from "../../Button";
import { ButtonGroup } from "../../ButtonGroup";
import { IconButton } from "../../IconButton";
import type { GallerySection } from "../Gallery";

function ButtonGroups() {
  return (
    <div className="gl-gallery-controls">
      <div className="gl-gallery-control-panel">
        <h3>Toolbar grouping</h3>
        <div className="gl-gallery-control-row">
          <ButtonGroup label="Editing actions">
            <IconButton icon="undo" label="Undo" shortcut="Ctrl+Z" />
            <IconButton icon="redo" label="Redo" shortcut="Ctrl+Shift+Z" />
            <IconButton icon="copy" label="Copy" shortcut="Ctrl+C" />
          </ButtonGroup>
          <ButtonGroup label="Document actions">
            <Button size="sm">Open library</Button>
            <Button size="sm" iconStart="download">Download file</Button>
          </ButtonGroup>
        </div>
      </div>
    </div>
  );
}

const section: GallerySection = { title: "ButtonGroup", order: 34, Component: ButtonGroups };
export default section;
