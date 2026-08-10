/**
 * ADR-012 stage 12.4: `prefers-reduced-motion` for the handful of viewport
 * animations CSS can't reach - React Flow's own zoom/pan transitions
 * (zoomIn/zoomOut/fitView/setCenter/setViewport) run through d3-zoom, driven
 * by an imperative `{ duration }` option at the CALL SITE, not a CSS
 * property - no stylesheet rule can touch them. Every CSS transition IS
 * reachable from one place and is handled there instead (see base.css's own
 * blanket `@media (prefers-reduced-motion: reduce)` rule).
 *
 * A plain synchronous check, not a React hook/live-subscribed value: every
 * call site here reads it once, at the moment a user action (a click, a
 * shortcut) requests an animation - there is no rendered UI whose OWN
 * appearance needs to react to the setting changing while the app is
 * already running, so there is nothing for a subscription to keep in sync.
 */
export function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** The duration to actually pass to a React Flow viewport animation
 * (zoomIn/zoomOut/fitView/setCenter/setViewport's own `{ duration }` option)
 * - `fullMs` when motion is fine, `0` (React Flow's own "jump, don't
 * animate" convention) when the user has asked for less of it. */
export function motionDuration(fullMs: number): number {
  return prefersReducedMotion() ? 0 : fullMs;
}
