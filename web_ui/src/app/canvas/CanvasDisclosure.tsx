import { useId, useState, type ReactNode } from "react";
import { Button } from "../ui/Button";
import { Icon } from "../ui/icons/Icon";
import "../ui/disclosure.css";
import "./CanvasDisclosure.css";

/** Canvas disclosure with the same kit presentation and accessible region.
 * The heading is supplied as visible button content, not a browser title. */
export function CanvasDisclosure({ label, children, className, open, defaultOpen = false, onOpenChange, disabled = false }: {
  label: string;
  children: ReactNode;
  className?: string;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  disabled?: boolean;
}) {
  const generated = useId();
  const [internal, setInternal] = useState(defaultOpen);
  const expanded = open ?? internal;
  const triggerId = generated + "-trigger";
  const panelId = generated + "-panel";

  const toggle = () => {
    const next = !expanded;
    if (open === undefined) setInternal(next);
    onOpenChange?.(next);
  };

  return <div className={["gl-disclosure", className].filter(Boolean).join(" ")}
    data-open={expanded ? "true" : undefined}>
    <Button type="button" variant="ghost" size="sm" className="gl-disclosure-trigger"
      id={triggerId} aria-expanded={expanded} aria-controls={panelId} disabled={disabled} onClick={toggle}>
      <Icon name="chevron-right" size="sm"
        className={expanded ? "gl-disclosure-chevron-open" : "gl-disclosure-chevron"} />
      {label}
    </Button>
    <div className="gl-disclosure-content" id={panelId} role="region" aria-labelledby={triggerId} hidden={!expanded}>
      {children}
    </div>
  </div>;
}
