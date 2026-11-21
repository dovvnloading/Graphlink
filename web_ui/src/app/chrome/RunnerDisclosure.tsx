import { useState, type ReactNode } from "react";

export function Disclosure({ label, children, className, onOpen, onToggle, initiallyOpen = false }: {
  label: string; children: ReactNode; className?: string; onOpen?: () => void;
  onToggle?: () => void; initiallyOpen?: boolean;
}) {
  const [open, setOpen] = useState(initiallyOpen);
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

