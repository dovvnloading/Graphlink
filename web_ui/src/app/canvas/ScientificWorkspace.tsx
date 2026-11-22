import { useStore } from "@xyflow/react";
import { createContext, memo, useCallback, useContext, useLayoutEffect, useState, type ComponentType, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Dialog, useOverlays } from "../overlays/overlays";
import type { ScientificFlowNode } from "./ScientificNodeView";
import { readScientificFocus } from "./scientificFocus";

type ScienceData = ScientificFlowNode["data"];
export interface ScienceEditorProps { id: string; data: ScienceData; requestedOperation?: string }
interface Slot { element: HTMLElement; data: ScienceData }
type RegisterSlot = (nodeId: string, slot: Slot | null) => void;
const Slots = createContext<RegisterSlot | null>(null);

export function useScientificSlots() { return useContext(Slots); }

/** Moving one persistent portal container keeps form state, pending promises, and
 * the actual file input intact. React never renders a second copy of the editor. */
const PlacedEditor = memo(function PlacedEditor({ target, Editor, ...props }: ScienceEditorProps & {
  target: HTMLElement | null; Editor: ComponentType<ScienceEditorProps>;
}) {
  const [container] = useState(() => {
    const element = document.createElement("div");
    element.className = "science-editor-content";
    return element;
  });
  useLayoutEffect(() => {
    if (target) target.appendChild(container);
    return () => { container.remove(); };
  }, [container, target]);
  return createPortal(<Editor {...props} />, container);
});

/** Only mounted card bodies and the focused node receive an editor. Focused
 * state survives canvas virtualization without turning it off for other nodes. */
export function ScientificWorkspace({ Editor, children }: {
  Editor: ComponentType<ScienceEditorProps>; children: ReactNode;
}) {
  const overlays = useOverlays();
  const focus = readScientificFocus(overlays.openSurface);
  const [slots, setSlots] = useState<ReadonlyMap<string, Slot>>(() => new Map());
  const [dialogBody, setDialogBody] = useState<HTMLDivElement | null>(null);
  useLayoutEffect(() => {
    const scroller = dialogBody?.closest<HTMLElement>(".overlay-dialog-body");
    if (!dialogBody || !scroller) return;
    const resize = new ResizeObserver(() => {
      const active = document.activeElement;
      if (!(active instanceof HTMLElement) || !dialogBody.contains(active)) return;
      const bounds = scroller.getBoundingClientRect();
      const field = active.getBoundingClientRect();
      if (field.top < bounds.top || field.bottom > bounds.bottom
          || field.left < bounds.left || field.right > bounds.right) {
        active.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
    });
    resize.observe(scroller);
    return () => resize.disconnect();
  }, [dialogBody]);
  const register = useCallback<RegisterSlot>((id, slot) => {
    setSlots(previous => {
      const old = previous.get(id);
      if (!slot && !old || slot && old?.element === slot.element && old.data === slot.data) return previous;
      const next = new Map(previous);
      if (slot) next.set(id, slot);
      else next.delete(id);
      return next;
    });
  }, []);
  const focusId = focus?.nodeId;
  const live = useStore(state => {
    const node = focusId ? state.nodeLookup.get(focusId) : undefined;
    return node?.type === "scientific" ? node.data as ScienceData : undefined;
  });
  const focusedData = live ?? (focusId ? slots.get(focusId)?.data : undefined);
  const ids = Array.from(slots.keys());
  if (focusId && focusedData && !slots.has(focusId)) ids.push(focusId);
  return <Slots.Provider value={register}>
    {children}
    {focus && overlays.openSurface && createPortal(
      <Dialog name={overlays.openSurface} title={focusedData ? focusedData.title + " workbench" : "Scientific workbench"}
        className="science-focused-dialog">
        <div className="science-body science-focus-body" ref={setDialogBody} />
        {!focusedData && <p role="status">The workbench is unavailable. Close this view to return to the graph.</p>}
      </Dialog>, document.body)}
    {ids.map(id => {
      const slot = slots.get(id);
      const focused = id === focusId;
      const data = focused ? focusedData : slot?.data;
      return data && <PlacedEditor key={id} id={id} data={data} Editor={Editor}
        target={focused ? dialogBody : slot?.element ?? null} requestedOperation={focused ? focus?.operationId : undefined} />;
    })}
  </Slots.Provider>;
}
