/**
 * ADR-012 stage 12.3: Shift+F10 / the ContextMenu key opens a node's menu via
 * the keyboard - the WAI-ARIA-conventional alternate trigger for a
 * right-click menu. Its own module (not inline in SceneCanvas.tsx's
 * useEffect) for the same reason as applyTheme.ts/connectionBadge.ts: lets a
 * test exercise the real DOM logic without mounting the whole canvas.
 *
 * Every node kind's own onContextMenu handler (11 of the 16 *NodeView.tsx
 * files - see NodeMenu.tsx's own stage-12.3 doc) already does the right
 * thing given a contextmenu EVENT; the gap is purely that one never reaches
 * them for a KEYBOARD invocation, because the actually-focused element when
 * a node is Tab-selected is React Flow's own node wrapper
 * (.react-flow__node, tabIndex=0 by default) - an ANCESTOR of that node's
 * own .scene-node div, not .scene-node itself or a descendant of it. A
 * native DOM event's target is whatever was focused; bubbling only ever
 * travels target -> ancestors, never back down into a target's own
 * descendants - so no keydown/contextmenu handler attached to .scene-node
 * (by any of the 11 files, or by React's own delegated dispatch) can ever
 * fire for an event whose target is .scene-node's PARENT. Dispatching a
 * fresh, real `contextmenu` MouseEvent directly AT .scene-node (found as
 * document.activeElement's own descendant) sidesteps that entirely: it
 * bubbles from exactly the right starting point, so every existing handler
 * runs completely unchanged - zero edits needed in any of the 11 files
 * themselves.
 */
export function isMenuKey(event: Pick<KeyboardEvent, "key" | "shiftKey">): boolean {
  return event.key === "ContextMenu" || (event.shiftKey && event.key === "F10");
}

/**
 * Handles one keydown event: if it's the menu key AND the currently focused
 * element contains a `.scene-node` descendant, calls `preventDefault()`
 * (suppressing whatever native OS/browser context menu Shift+F10 would
 * otherwise also try to open on the focused wrapper - the same "keydown has
 * a browser default action, preventDefault() on IT suppresses that action"
 * contract Enter/Space/ArrowKeys already rely on elsewhere in this app) and
 * dispatches a synthetic `contextmenu` MouseEvent at that node. No-ops
 * (returns false) for every other key, or when focus isn't on a node at all
 * - callers should let the browser's own default happen in that case.
 */
export function handleKeyboardContextMenu(event: KeyboardEvent): boolean {
  if (!isMenuKey(event)) return false;
  const active = document.activeElement;
  // document.body is the BROWSER'S OWN default activeElement whenever
  // nothing else has meaningfully taken focus (nothing focused yet, or
  // focus was just cleared) - without this guard, body's querySelector
  // would find the FIRST .scene-node anywhere in the whole page (every
  // node is a body descendant), opening an arbitrary node's menu instead
  // of correctly doing nothing when the user isn't actually on any node.
  if (!(active instanceof HTMLElement) || active === document.body) return false;
  const node = active.querySelector<HTMLElement>(".scene-node");
  if (!node) return false;
  event.preventDefault();
  const rect = node.getBoundingClientRect();
  node.dispatchEvent(
    new MouseEvent("contextmenu", {
      bubbles: true,
      cancelable: true,
      clientX: rect.left + 16,
      clientY: rect.top + 16,
    }),
  );
  return true;
}
