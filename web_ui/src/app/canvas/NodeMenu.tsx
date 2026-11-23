import type { ReactNode, RefObject } from "react";
import { ContextMenu } from "../ui/Menu";
import type { MenuPosition } from "./menuPosition";

/** Canvas compatibility wrapper: existing node views keep their pointer,
 * kebab-button and keyboard contextmenu handlers and raw role=menuitem
 * children until their individual kit migrations. */
export function NodeMenu({
  position, onClose, className, ignoreRef, ariaLabel, children,
}: {
  position: MenuPosition;
  onClose: () => void;
  className?: string;
  ariaLabel?: string;
  ignoreRef?: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  return (
    <ContextMenu position={position} onClose={onClose} className={className}
      ignoreRef={ignoreRef} ariaLabel={ariaLabel} preserveNodePlacement>
      {children}
    </ContextMenu>
  );
}
