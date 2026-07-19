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
