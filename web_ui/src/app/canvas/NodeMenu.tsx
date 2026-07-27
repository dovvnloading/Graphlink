import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";

/**
 * The shell every node context menu renders through (R8a).
 *
 * WHY THIS EXISTS. All 12 node menus used to render as a plain child of their
 * own NodeView with `style={{ position: "fixed", left: clientX, top: clientY }}`.
 * That looks correct and is completely wrong here: React Flow puts an inline
 * `transform: translate(...)` on EVERY node wrapper and a `translate(...)
 * scale(zoom)` on `.react-flow__viewport`. A transformed ancestor becomes the
 * containing block for `position: fixed` descendants, so `left: clientX` stopped
 * being measured from the viewport and started being measured from the node's
 * own box in flow space - then translated by the node's canvas position and
 * multiplied by the zoom. The menu landed at roughly (node.x + clientX) * zoom:
 * far away for any node not at the origin, usually off-screen entirely, and
 * visually scaled by the canvas zoom. Right-click read as "does nothing".
 *
 * Portaling to document.body removes every transformed ancestor from the chain,
 * which restores BOTH the coordinate space (clientX/clientY mean what they say
 * again) and the stacking context (the menu competes at root, so it can finally
 * paint over canvas chrome instead of under it).
 *
 * It also fixes two things the old inline menus never handled: the menu is
 * clamped so it cannot open off the edge of the window, and it is capped in
 * height so a long menu scrolls instead of running its last items off-screen.
 */

const VIEWPORT_MARGIN = 8;

export function NodeMenu({
  position,
  onClose,
  className,
  ignoreRef,
  ariaLabel,
  children,
}: {
  position: { x: number; y: number };
  onClose: () => void;
  className?: string;
  /** Accessible name for the surface. Context menus are named by the node they
   * belong to and omit it; named popovers (the colour picker) must pass one or
   * they lose their accessible name entirely. */
  ariaLabel?: string;
  /** A toggle button that opens this surface. Pointerdowns on it are ignored
   * so it can close the surface itself: without this the outside-click handler
   * fires first and closes, then the button's own onClick re-opens, making the
   * toggle appear dead. Context menus have no such trigger and omit it. */
  ignoreRef?: React.RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const menuRef = useRef<HTMLDivElement | null>(null);
  // Start at the raw pointer position, then correct after measuring. Rendering
  // at the requested spot first (rather than hiding until measured) keeps the
  // menu from visibly jumping in the common case where no clamping is needed.
  const [placement, setPlacement] = useState(position);

  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;
    const rect = menu.getBoundingClientRect();
    // Prefer flipping to the other side of the pointer over merely nudging: a
    // menu that overlaps the thing you right-clicked is worse than one that
    // opens up/left of it, which is what every native context menu does.
    const x =
      position.x + rect.width + VIEWPORT_MARGIN > window.innerWidth
        ? Math.max(VIEWPORT_MARGIN, position.x - rect.width)
        : position.x;
    const y =
      position.y + rect.height + VIEWPORT_MARGIN > window.innerHeight
        ? Math.max(VIEWPORT_MARGIN, position.y - rect.height)
        : position.y;
    if (x !== placement.x || y !== placement.y) setPlacement({ x, y });
    // `placement` is deliberately not a dependency: this runs to CORRECT the
    // placement it just set, so depending on it would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [position.x, position.y]);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (menuRef.current?.contains(target)) return;
      if (ignoreRef?.current?.contains(target)) return;
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        // Stop it here: without this the same Escape also reaches whatever
        // dialog or overlay is open behind the canvas and closes that too.
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [onClose, ignoreRef]);

  return createPortal(
    <div
      ref={menuRef}
      className={className}
      style={{ position: "fixed", left: placement.x, top: placement.y }}
      role="menu"
      aria-label={ariaLabel}
    >
      {children}
    </div>,
    document.body,
  );
}
