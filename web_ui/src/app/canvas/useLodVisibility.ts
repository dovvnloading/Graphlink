/**
 * LOD is now a canvas-root CSS concern, not a per-node React subscription.
 * Keep this compatibility hook while individual views continue calling it:
 * their explicit user collapse state still works, but zoom never re-renders
 * a content view. NodeShell switches body/overview below 50% and icon-only
 * below 25% via --gl-canvas-zoom.
 */
export function useLodVisibility(): boolean {
  return false;
}
