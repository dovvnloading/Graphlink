// Keep the shared navigation/drag measurement helper independent of the
// interactive canvas chunk: global shortcuts need it before the canvas loads.
export type MeasuredSizeSource = { getInternalNode: (id: string) => { measured?: { width?: number; height?: number } } | undefined };
export function measuredNodeSize(reactFlow: MeasuredSizeSource, id: string): { width: number; height: number } | null {
  const measured = reactFlow.getInternalNode(id)?.measured;
  if (measured?.width !== undefined && measured?.height !== undefined) {
    return { width: measured.width, height: measured.height };
  }
  const el = document.querySelector(`.react-flow__node[data-id="${CSS.escape(id)}"]`);
  if (!(el instanceof HTMLElement) || el.offsetWidth === 0) return null;
  return { width: el.offsetWidth, height: el.offsetHeight };
}
