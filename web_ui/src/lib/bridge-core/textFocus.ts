/**
 * Whether the DOM currently has a text-editable element focused - the
 * classifier App.tsx's own global keydown handler gates gated shortcuts on
 * (isGatedWhileTyping(id) && isTextEditable(document.activeElement)), the
 * single-process successor to the pre-Qt-removal keyboard arbitration
 * protocol this file used to also carry (a QWebChannel report to the Python
 * side's AcceleratorForwardingFilter/ChatView.keyPressEvent, retired along
 * with the rest of that bridge - see lib/ws/transport.ts's own module doc).
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
