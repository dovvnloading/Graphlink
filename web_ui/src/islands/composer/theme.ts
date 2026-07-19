import { useEffect } from "react";

/**
 * Applies a --gl-* custom property map to a target element (document.documentElement
 * in real use). Extracted from ComposerApp's effect so it is testable without going
 * through the full bridge/React lifecycle - jsdom's default mock bridge state carries
 * an empty cssVariables object (see bridgeTypes.ts), so an integration-level render
 * test alone could never exercise real values landing on the DOM.
 */
export function applyThemeCssVariables(
  target: HTMLElement,
  cssVariables: Record<string, string>,
): void {
  for (const [name, value] of Object.entries(cssVariables)) {
    target.style.setProperty(name, value);
  }
}

/**
 * Re-applies cssVariables to `target` whenever their CONTENT changes, not
 * whenever the object reference changes.
 *
 * Every bridge snapshot is a fresh JSON.parse() (bridge.ts's parseState()),
 * so a new cssVariables object arrives on EVERY publish - including every
 * keystroke, since updateDraft() republishes the full state. Depending on
 * the object directly would re-run all ~77 setProperty() calls on every
 * keystroke, not just on a genuine theme change. Keying on the stringified
 * content instead gives React a value it can actually compare by equality;
 * stringifying ~77 short entries is microseconds - cheap enough to do on
 * every render in exchange for skipping the DOM writes when nothing changed.
 */
export function useAppliedThemeCssVariables(
  target: HTMLElement,
  cssVariables: Record<string, string>,
): void {
  const key = JSON.stringify(cssVariables);
  useEffect(() => {
    applyThemeCssVariables(target, cssVariables);
    // key IS the real dependency; including cssVariables/target directly
    // would defeat the point of keying on content instead of reference.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}
