import { useId, useState, type ReactNode } from "react";
import { Icon } from "./icons/Icon";
import "./disclosure.css";

export type DisclosureProps = {
  title: string;
  children: ReactNode;
  variant?: "inline" | "section";
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  disabled?: boolean;
  className?: string;
};

export function Disclosure({
  title, children, variant = "inline", open, defaultOpen = false,
  onOpenChange, disabled = false, className,
}: DisclosureProps) {
  const [internal, setInternal] = useState(defaultOpen);
  const expanded = open ?? internal;
  const generated = useId();
  const buttonId = generated + "-trigger";
  const panelId = generated + "-panel";
  function toggle() {
    const next = !expanded;
    if (open === undefined) setInternal(next);
    onOpenChange?.(next);
  }

  return (
    <div className={["gl-disclosure", variant === "section" && "gl-disclosure-section", className].filter(Boolean).join(" ")}>
      <button className="gl-disclosure-trigger" id={buttonId} type="button"
        aria-expanded={expanded} aria-controls={panelId} disabled={disabled} onClick={toggle}>
        <Icon name="chevron-right" size="sm" className={expanded ? "gl-disclosure-chevron-open" : "gl-disclosure-chevron"} />
        <span>{title}</span>
      </button>
      <div className="gl-disclosure-content" id={panelId} role="region" aria-labelledby={buttonId} hidden={!expanded}>
        {children}
      </div>
    </div>
  );
}
