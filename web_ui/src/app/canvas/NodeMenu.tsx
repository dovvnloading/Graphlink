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
 *
 * ADR-012 stage 12.3: full WAI-ARIA menu keyboard pattern, added here once
 * rather than in each of the 11 callers - every caller renders its items as
 * plain `<button role="menuitem">` children with no shared item-list shape,
 * so this component's own DOM (menuRef's subtree) is the only place that can
 * see the CURRENT set of items regardless of which caller is open or which
 * conditionally-rendered submenu (ChatNodeMenu's Mark Status/Generate Chart)
 * is expanded at the moment. On open: focus moves to the first menuitem
 * (matches every OS's own native context menu). Arrow Up/Down rove focus
 * among items, wrapping; Home/End jump to the first/last. On close (by
 * ANY path - Escape, outside click, or an item's own onClick calling
 * onClose): focus returns to whatever had it before the menu opened, so a
 * keyboard user doing Shift+F10 -> arrow to an item -> Enter never loses
 * their place on the canvas. Re-queries `[role="menuitem"]` on every
 * keypress rather than caching the list once, so a toggled-open submenu's
 * items are picked up (and a toggled-closed one's are correctly skipped)
 * without this component needing to know submenus exist at all.
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

  // ADR-012 stage 12.3: captured once, at mount - this is whatever the
  // document's active element was the instant BEFORE this menu's own portal
  // took focus, i.e. the trigger (a node card opened via Shift+F10, or a
  // toggle button for the surfaces that pass ignoreRef). Restored on
  // unmount below. A ref (not state) because this is a write-once-read-once
  // value with no rendering implication of its own.
  const triggerRef = useRef<HTMLElement | null>(
    document.activeElement instanceof HTMLElement ? document.activeElement : null,
  );

  useEffect(() => {
    // Move focus into the menu once, on mount - every native OS context menu
    // and every WAI-ARIA menu pattern implementation does this regardless of
    // whether the menu was opened by mouse or keyboard, so a screen-reader
    // user doesn't have to guess that a new interactive surface appeared.
    const first = menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]');
    first?.focus();
    // Captured into a local, not read via triggerRef.current directly in the
    // cleanup below - triggerRef itself never gets reassigned after mount
    // (nothing else in this component writes to it), but react-hooks/
    // exhaustive-deps can't see that invariant, and copying into the
    // closure is the standard, genuinely-correct fix rather than a
    // suppression of a real "ref may have changed" class of bug.
    const trigger = triggerRef.current;
    return () => {
      // Restore focus to the trigger on unmount, via ANY close path -
      // Escape, an outside click, or an item's own onClick calling onClose.
      // Skipped if the trigger is gone (e.g. its node was deleted while the
      // menu was open) - focus() on a detached element is a silent no-op in
      // every browser, so the `isConnected` guard is belt-and-suspenders,
      // not load-bearing, but makes the intent explicit.
      if (trigger?.isConnected) trigger.focus();
    };
  }, []);

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
        return;
      }
      // ADR-012 stage 12.3: Arrow/Home/End roving-focus among the CURRENTLY
      // rendered menuitems - only reachable while focus is actually inside
      // this menu (the mount effect above puts it there on open), so a
      // stray arrow-key press elsewhere on the page is never intercepted.
      if (!menuRef.current?.contains(document.activeElement)) return;
      const items = Array.from(menuRef.current.querySelectorAll<HTMLElement>('[role="menuitem"]'));
      if (items.length === 0) return;
      const currentIndex = items.indexOf(document.activeElement as HTMLElement);
      let nextIndex: number | null = null;
      if (event.key === "ArrowDown") nextIndex = currentIndex < items.length - 1 ? currentIndex + 1 : 0;
      else if (event.key === "ArrowUp") nextIndex = currentIndex > 0 ? currentIndex - 1 : items.length - 1;
      else if (event.key === "Home") nextIndex = 0;
      else if (event.key === "End") nextIndex = items.length - 1;
      if (nextIndex === null) return;
      event.preventDefault();
      event.stopPropagation();
      items[nextIndex].focus();
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
