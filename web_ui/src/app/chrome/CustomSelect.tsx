import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";

/**
 * A custom-styled select, matching the app's existing custom dropdown
 * convention (Composer.tsx's own Reasoning/Model pickers) rather than a
 * native OS `<select>` - built for Settings' own selects (API Provider,
 * per-task Ollama model mode, Llama.cpp scanned-model pickers), which were
 * the one place in the app still using bare `<select className="settings-
 * select">` while everywhere else (Composer, View/Plugins/Pins) already has
 * a styled, app-consistent dropdown.
 *
 * Deliberately NOT built on the shared Popover/useOverlays() system
 * (../overlays/overlays), even though every other floating panel in the app
 * is. That system is single-open BY DESIGN (opening any one surface closes
 * whatever else is open) - correct for app-chrome popovers that are never
 * expected to coexist with a modal dialog, but WRONG here: this component's
 * whole reason to exist is being usable INSIDE an already-open Dialog
 * (Settings). Routing through useOverlays() would make opening a dropdown
 * flip the registry's openSurface away from "settings", which Settings'
 * own <Dialog name="settings"> reads as "I'm not open anymore" and
 * unmounts entirely - the same class of bug CodeExecutionApprovalPanel.tsx's
 * own module doc already identified and avoided for an analogous reason
 * (two of ITS panels can be open at once; here, one of THESE needs to be
 * open ALONGSIDE an already-open dialog). This component keeps its own
 * fully independent open/closed state instead, mirroring NodeMenu.tsx's
 * posture (also deliberately outside the overlay registry, for the same
 * "must nest inside/alongside other things" reason) rather than Popover's.
 *
 * Visually reuses .overlay-popover/.overlay-popover-anchored and
 * .reasoning-option/-label/-description VERBATIM for the panel and its
 * option rows - not a lookalike reimplementation, the literal same classes
 * Composer's own Reasoning/Model pickers render through Popover, so this is
 * pixel-identical to the rest of the app's custom dropdowns by
 * construction. Only the trigger button (.custom-select-trigger) is new
 * CSS, styled to fill the same box .settings-select's native <select> used
 * to occupy.
 *
 * Positioning mirrors overlays.tsx's own useAnchoredPlacement (flip above
 * the trigger if there's no room below, clamp horizontally) but reads a
 * local ref directly instead of a named data-overlay-trigger lookup, since
 * this component has no registry entry to look itself up by.
 */

const ANCHOR_MARGIN = 8;
const ANCHOR_GAP = 4;

export interface CustomSelectOption {
  id: string;
  label: string;
  description?: string;
}

export function CustomSelect({
  value,
  options,
  onChange,
  ariaLabel,
  disabled,
  placeholder = "Select…",
}: {
  value: string;
  options: CustomSelectOption[];
  onChange: (id: string) => void;
  ariaLabel: string;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [placement, setPlacement] = useState<{ top: number; left: number; minWidth: number } | null>(null);

  useLayoutEffect(() => {
    if (!open) return;
    const trigger = triggerRef.current;
    const panel = panelRef.current;
    if (!trigger || !panel) return;
    const triggerRect = trigger.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const top =
      triggerRect.bottom + panelRect.height + ANCHOR_GAP > window.innerHeight
        ? Math.max(ANCHOR_MARGIN, triggerRect.top - panelRect.height - ANCHOR_GAP)
        : triggerRect.bottom + ANCHOR_GAP;
    const left = Math.min(
      Math.max(ANCHOR_MARGIN, triggerRect.left),
      window.innerWidth - panelRect.width - ANCHOR_MARGIN,
    );
    setPlacement({ top, left, minWidth: triggerRect.width });
  }, [open]);

  useOutsideDismiss(open, triggerRef, panelRef, () => setOpen(false));

  const selected = options.find((option) => option.id === value);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="custom-select-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => setOpen((isOpen) => !isOpen)}
      >
        <span className="custom-select-value">{selected ? selected.label : placeholder}</span>
        <ChevronIcon />
      </button>
      {open &&
        createPortal(
          <div
            ref={panelRef}
            role="dialog"
            aria-modal="false"
            aria-label={ariaLabel}
            tabIndex={-1}
            className="overlay-popover overlay-popover-anchored custom-select-panel"
            style={placement ? { top: placement.top, left: placement.left, minWidth: placement.minWidth } : undefined}
          >
            {options.map((option) => (
              <button
                key={option.id}
                type="button"
                className={"reasoning-option" + (option.id === value ? " active" : "")}
                onClick={() => {
                  onChange(option.id);
                  setOpen(false);
                  triggerRef.current?.focus();
                }}
              >
                <span className="reasoning-option-label">{option.label}</span>
                {option.description && <span className="reasoning-option-description">{option.description}</span>}
              </button>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}

function ChevronIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="icon custom-select-chevron">
      <path d="m7 10 5 5 5-5" />
    </svg>
  );
}

// Outside-pointerdown + Escape dismiss, scoped to this instance's own
// trigger/panel refs - deliberately NOT the shared overlays.tsx listeners
// (see this file's own module doc for why). Escape calls
// event.preventDefault() so the SAME keypress doesn't also bubble up into
// overlays.tsx's own document-level Escape handler and close the whole
// parent Dialog (Settings) - that handler explicitly checks
// event.defaultPrevented for exactly this "something more specific already
// claimed it" case (see its own comment).
function useOutsideDismiss(
  open: boolean,
  triggerRef: RefObject<HTMLButtonElement | null>,
  panelRef: RefObject<HTMLDivElement | null>,
  close: () => void,
) {
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      const target = event.target as Node;
      if (panelRef.current?.contains(target)) return;
      if (triggerRef.current?.contains(target)) return;
      close();
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      close();
      triggerRef.current?.focus();
    }
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
}
