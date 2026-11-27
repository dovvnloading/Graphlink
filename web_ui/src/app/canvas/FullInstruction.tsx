import { useState } from "react";
import { copyText } from "../clipboard";
import { Button } from "../ui/Button";
import { Disclosure } from "../ui/Disclosure";
import "./FullInstruction.css";

/**
 * The complete Builder goal or Agent task, which a card header shows only
 * as one clipped line (#1158). Closed by default so dense canvases stay
 * compact; the text keeps its newlines and stays selectable.
 */
export function FullInstruction({ label, text }: { label: "Goal" | "Task"; text: string }) {
  // Mounted only while open: a closed card carries no second copy of a
  // potentially long instruction.
  const [open, setOpen] = useState(false);
  return (
    <Disclosure className="node-full-instruction nodrag" title={`Full ${label.toLowerCase()}`}
      open={open} onOpenChange={setOpen}>
      {open && <>
        <p className="node-full-instruction-text nowheel">{text}</p>
        <Button size="sm" variant="ghost" iconStart="copy" className="nodrag"
          onClick={() => { void copyText(text, `the ${label.toLowerCase()}`, { announce: true }); }}>
          Copy {label.toLowerCase()}
        </Button>
      </>}
    </Disclosure>
  );
}
