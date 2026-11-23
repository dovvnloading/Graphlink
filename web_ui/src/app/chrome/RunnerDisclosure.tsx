import { useState, type ReactNode } from "react";
import { Disclosure as KitDisclosure } from "../ui/Disclosure";

/** Preserve run-inspector callbacks while sharing the kit's accessible disclosure. */
export function Disclosure({ label, children, className, onOpen, onToggle, initiallyOpen = false }: {
  label: string; children: ReactNode; className?: string; onOpen?: () => void;
  onToggle?: () => void; initiallyOpen?: boolean;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <KitDisclosure title={label} open={open} className={className}
      onOpenChange={(next) => {
        setOpen(next);
        onToggle?.();
        if (next) onOpen?.();
      }}>
      {children}
    </KitDisclosure>
  );
}
