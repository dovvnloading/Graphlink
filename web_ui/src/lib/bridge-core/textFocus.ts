/**
 * Reports whether the island's own DOM currently has a text-editable
 * element focused, over the "islandHost" QWebChannel object every
 * WebIslandHost registers (graphlink_web_island_host.py). This is the JS
 * half of the keyboard arbitration protocol: the Python side uses this one
 * boolean, aggregated across every registered island, to decide whether
 * global QShortcuts should fire and whether the canvas view should steal
 * WASD/arrow keys - see AcceleratorForwardingFilter and ChatView.keyPressEvent.
 *
 * Generic, bridge-core - not composer-specific. A second island's own
 * bridge.ts needs only one line inside its existing connectQWebChannel
 * callback (installTextFocusReporting(objects)); this file owns the DOM
 * listening and the classification logic once, for every island.
 */

const NON_TEXT_INPUT_TYPES = new Set([
  "button",
  "checkbox",
  "color",
  "file",
  "hidden",
  "image",
  "radio",
  "range",
  "reset",
  "submit",
]);

/**
 * Whether `el` is an element a user could reasonably type text into.
 * Exclusion-list on <input type=...> (rather than an inclusion list of
 * "known text types") deliberately: it also covers date/month/week/time/
 * datetime-local and any future text-like input type without needing this
 * list updated - only the genuinely non-text control types are excluded.
 */
export function isTextEditable(el: Element | null): boolean {
  if (!el) return false;
  if (el.tagName === "TEXTAREA" || el.tagName === "SELECT") return true;
  if (el.tagName === "INPUT") {
    const type = (el as HTMLInputElement).type.toLowerCase();
    return !NON_TEXT_INPUT_TYPES.has(type);
  }
  return (el as HTMLElement).isContentEditable === true;
}

interface IslandHostRemote {
  reportTextFocus(hasFocus: boolean): void;
}

/**
 * Attaches capture-phase focusin/focusout listeners at the document root and
 * calls objects.islandHost.reportTextFocus(bool) on every genuine
 * transition. A no-op if `objects.islandHost` isn't present (the mock-bridge
 * dev path, or an island that hasn't registered one) - callers don't need to
 * guard this themselves.
 *
 * focusout is deferred one microtask before re-evaluating
 * document.activeElement, rather than reporting false synchronously: tabbing
 * between two text fields in the same island fires focusout(field A) then
 * focusin(field B) on the same tick, and without the defer that would
 * report a spurious false-then-true blink instead of staying continuously
 * true. pagehide/visibilitychange are defensive backstops for teardown
 * paths that don't fire a clean blur (e.g. the page being torn down while
 * a field is focused).
 */
export function installTextFocusReporting(
  objects: Record<string, unknown>,
  doc: Document = document,
): void {
  const islandHost = objects.islandHost as IslandHostRemote | undefined;
  if (!islandHost || typeof islandHost.reportTextFocus !== "function") return;

  let last: boolean | null = null;
  const report = (value: boolean) => {
    if (value === last) return;
    last = value;
    islandHost.reportTextFocus(value);
  };

  doc.addEventListener("focusin", () => report(isTextEditable(doc.activeElement)), true);
  doc.addEventListener(
    "focusout",
    () => {
      queueMicrotask(() => report(isTextEditable(doc.activeElement)));
    },
    true,
  );
  doc.defaultView?.addEventListener("pagehide", () => report(false));
  doc.addEventListener("visibilitychange", () => {
    if (doc.hidden) report(false);
  });
}
