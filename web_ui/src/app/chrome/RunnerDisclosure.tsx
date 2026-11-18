import { useState, type ReactNode } from "react";

export function Disclosure({ label, children, className, onOpen, onToggle }: {
  label: string; children: ReactNode; className?: string; onOpen?: () => void; onToggle?: () => void;
}) {
  const [open, setOpen] = useState(false);
  return <div className={className}>
    <button type="button" className="runner-disclosure" aria-expanded={open} onClick={() => {
      setOpen(!open);
      onToggle?.();
      if (!open) onOpen?.();
    }}>
      <span aria-hidden="true">{open ? "▾" : "▸"}</span> {label}
    </button>
    {open && children}
  </div>;
}

