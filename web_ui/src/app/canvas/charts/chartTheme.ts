/**
 * ADR-013 stage 13.2: the client renderer's theme source. Reads the SAME
 * `--gl-*` custom properties graphlink_web_island_host.py injects into the
 * page <head> at construction time (see gl-theme.css/gl-vars-dev.css) rather
 * than hardcoding hex - so this renderer is automatically correct for
 * whichever theme is actually active.
 *
 * This module used to claim "zero dependency on ADR-012's (still-proposed)
 * theme toggle ever shipping". That premise expired: ADR-012 stage 12.2
 * shipped a real live toggle the same day this file landed. Reading the
 * tokens is still the right approach, but it is no longer sufficient on its
 * own - a chart mounted before a theme switch would keep the old palette
 * forever, since nothing re-read these values. chartHooks.ts's useChartTheme
 * now carries the MutationObserver + matchMedia listeners that keep an open
 * chart in sync; see its own comment for why both are required.
 *
 * The categorical series palette deliberately does NOT reuse `--gl-frame-*`:
 * those tokens are near-identical grays by design in the current theme (see
 * GroupColorPicker.tsx's own module doc - confirmed there, not re-derived
 * here) and would render a bar/pie/sankey chart with indistinguishable
 * series. GROUP_NAMED_COLORS is this app's own already-established
 * "visually distinct hues" palette (used for note/frame/container color-
 * coding) - reusing it keeps the chart's palette consistent with the rest
 * of the canvas instead of inventing a second one.
 */

import { GROUP_NAMED_COLORS } from "../GroupColorPicker";

export interface ChartTheme {
  categorical: string[];
  text: string;
  textMuted: string;
  border: string;
  gridline: string;
  surface: string;
  tooltipBackground: string;
  tooltipBorder: string;
  fontFamily: string;
}

/** Values mirror gl-vars-dev.css's own dark-theme defaults - used only
 * before the first live read (SSR-safe, and jsdom-safe for tests that don't
 * bother stubbing computed styles), never trusted over an actual read. */
export const FALLBACK_CHART_THEME: ChartTheme = {
  categorical: GROUP_NAMED_COLORS.map((color) => color.hex),
  text: "#F1F1F1",
  textMuted: "#A4A4A4",
  border: "#3F3F3F",
  gridline: "#424242",
  surface: "#1E1E1E",
  tooltipBackground: "#121212",
  tooltipBorder: "#505050",
  fontFamily: "'Segoe UI', sans-serif",
};

function readVar(style: CSSStyleDeclaration, name: string, fallback: string): string {
  const value = style.getPropertyValue(name).trim();
  return value || fallback;
}

/** Reads the live theme off `element`'s computed style. Callers pass the
 * chart's own DOM node (or any descendant of the island root) so this picks
 * up the values actually cascading to it, rather than assuming
 * `document.documentElement` carries them. */
export function readChartTheme(element: Element): ChartTheme {
  const style = getComputedStyle(element);
  return {
    categorical: FALLBACK_CHART_THEME.categorical,
    text: readVar(style, "--gl-surface-text-primary", FALLBACK_CHART_THEME.text),
    textMuted: readVar(style, "--gl-surface-text-muted", FALLBACK_CHART_THEME.textMuted),
    border: readVar(style, "--gl-surface-border", FALLBACK_CHART_THEME.border),
    gridline: readVar(style, "--gl-surface-divider", FALLBACK_CHART_THEME.gridline),
    surface: readVar(style, "--gl-surface-node-body", FALLBACK_CHART_THEME.surface),
    tooltipBackground: readVar(style, "--gl-surface-inset-deep", FALLBACK_CHART_THEME.tooltipBackground),
    tooltipBorder: readVar(style, "--gl-surface-border-strong", FALLBACK_CHART_THEME.tooltipBorder),
    fontFamily: readVar(style, "--gl-font-family", FALLBACK_CHART_THEME.fontFamily),
  };
}
