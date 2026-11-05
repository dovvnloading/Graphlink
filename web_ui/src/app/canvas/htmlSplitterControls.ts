import { HTML_SPLIT_MAX, HTML_SPLIT_MIN } from "./canvasConstants";

/** An accessible separator follows the WAI-ARIA window-splitter key pattern.
 * Arrow keys move one 5% step; PageUp/PageDown move 20%; Home/End jump to
 * the allowed bounds. No keys with modifiers are handled by the view.
 *
 * Pure and decimal-quantized so repeated key presses never accumulate
 * 0.55000000000000004-style rounding drift in persisted splitter values.
 * Null means this is NOT a splitter key and the caller must leave it alone.
 */
export function nextHtmlSplitterKeyboardValue(current: number, key: string): number | null {
  let next: number;
  switch (key) {
    case "ArrowUp":
      next = current - 0.05;
      break;
    case "ArrowDown":
      next = current + 0.05;
      break;
    case "PageUp":
      next = current - 0.2;
      break;
    case "PageDown":
      next = current + 0.2;
      break;
    case "Home":
      next = HTML_SPLIT_MIN;
      break;
    case "End":
      next = HTML_SPLIT_MAX;
      break;
    default:
      return null;
  }
  return Math.round(Math.max(HTML_SPLIT_MIN, Math.min(HTML_SPLIT_MAX, next)) * 100) / 100;
}
