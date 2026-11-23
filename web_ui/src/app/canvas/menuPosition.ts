/** Viewport-space coordinates used for every node's context menu. */
export interface MenuPosition {
  x: number;
  y: number;
}

export const MENU_VIEWPORT_MARGIN = 8;
export const SUBMENU_GAP = 4;

type Size = Pick<DOMRect, "width" | "height">;
type Rect = Pick<DOMRect, "left" | "top" | "right" | "bottom" | "width" | "height">;

function clamp(value: number, size: number, viewport: number, margin: number): number {
  return Math.max(margin, Math.min(value, Math.max(margin, viewport - size - margin)));
}

/** Flip around the requested pointer first, then clamp to the visible viewport.
 * The surface itself caps its height and scrolls, so it never grows beyond
 * the available space even if there are many items. */
export function clampMenuPosition(
  requested: MenuPosition,
  menu: Size,
  viewport = { width: window.innerWidth, height: window.innerHeight },
): MenuPosition {
  const margin = MENU_VIEWPORT_MARGIN;
  const x = requested.x + menu.width + margin > viewport.width
    ? requested.x - menu.width : requested.x;
  const y = requested.y + menu.height + margin > viewport.height
    ? requested.y - menu.height : requested.y;
  return {
    x: clamp(x, menu.width, viewport.width, margin),
    y: clamp(y, menu.height, viewport.height, margin),
  };
}

/** Exact pre-kit NodeMenu placement: flip at the same 8px collision threshold,
 * floor flipped coordinates at 8px, but do not apply an additional far-edge
 * clamp or nudge unflipped coordinates. All canvas node menus use this path. */
export function placeLegacyNodeMenu(
  requested: MenuPosition,
  menu: Size,
  viewport = { width: window.innerWidth, height: window.innerHeight },
): MenuPosition {
  const margin = MENU_VIEWPORT_MARGIN;
  return {
    x: requested.x + menu.width + margin > viewport.width
      ? Math.max(margin, requested.x - menu.width) : requested.x,
    y: requested.y + menu.height + margin > viewport.height
      ? Math.max(margin, requested.y - menu.height) : requested.y,
  };
}

/** A submenu prefers the right side of its parent item, flips left if needed,
 * and clamps vertically rather than running off the bottom of the screen. */
export function clampSubMenuPosition(
  anchor: Rect,
  menu: Size,
  viewport = { width: window.innerWidth, height: window.innerHeight },
): MenuPosition {
  const margin = MENU_VIEWPORT_MARGIN;
  const right = anchor.right + SUBMENU_GAP;
  const x = right + menu.width + margin > viewport.width
    ? anchor.left - menu.width - SUBMENU_GAP : right;
  return {
    x: clamp(x, menu.width, viewport.width, margin),
    y: clamp(anchor.top, menu.height, viewport.height, margin),
  };
}
