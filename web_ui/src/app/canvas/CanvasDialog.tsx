import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useFocusEscapeBackstop, useOverlays } from "../overlays/overlays";
import { IconButton } from "../ui/IconButton";

/** Keep the existing overlay manager, modal semantics and kit close control.
 * Canvas callers provide a visible heading instead of a native title attribute. */
const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function CanvasDialog({ name, heading, className, children }: {
  name: string; heading: string; className?: string; children: ReactNode;
}) {
  const overlays = useOverlays();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const headingRef = useRef<HTMLSpanElement | null>(null);
  const headingId = useId();
  const isOpen = overlays.isOpen(name);
  const closing = overlays.isClosing(name);
  const topmost = overlays.isTopmost(name);

  useEffect(() => {
    if (!isOpen) return;
    overlays.registerSurfaceElement(name, panelRef.current);
    return () => overlays.registerSurfaceElement(name, null);
  }, [isOpen, name, overlays.registerSurfaceElement]);

  useEffect(() => {
    if (!isOpen) return;
    const panel = panelRef.current;
    if (!panel) return;
    const preferred = panel.querySelector<HTMLElement>("[data-autofocus]:not([disabled])");
    const field = panel.querySelector<HTMLElement>(
      'input:not([disabled]):not([type="hidden"]), textarea:not([disabled]), select:not([disabled])',
    );
    (preferred ?? field ?? headingRef.current)?.focus();
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen || !topmost) return;
    const panel = panelRef.current;
    if (!panel) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const focusables = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!focusables.length) {
        event.preventDefault();
        headingRef.current?.focus();
        return;
      }
      const first = focusables[0], last = focusables[focusables.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === headingRef.current)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    panel.addEventListener("keydown", onKeyDown);
    return () => panel.removeEventListener("keydown", onKeyDown);
  }, [isOpen, topmost]);

  useFocusEscapeBackstop(panelRef, isOpen && topmost, FOCUSABLE);

  if (!isOpen && !closing) return null;
  const shell = <div className="overlay-scrim" data-state={closing ? "closing" : "open"}
    data-nested={overlays.isNested(name) ? "" : undefined}
    aria-hidden={closing ? true : undefined}
    onPointerDown={(event) => {
      if (isOpen && topmost && event.target === event.currentTarget) overlays.closeLayer(name);
    }}>
    <div ref={panelRef} role="dialog" aria-modal="true" aria-labelledby={headingId}
      data-state={closing ? "closing" : "open"} data-size="md" tabIndex={-1}
      className={["overlay-dialog", className].filter(Boolean).join(" ")}>
      <header className="overlay-dialog-header">
        <div className="overlay-dialog-heading">
          <span ref={headingRef} id={headingId} role="heading" aria-level={2}
            tabIndex={-1} className="overlay-dialog-title">{heading}</span>
        </div>
        {isOpen && <IconButton icon="close" className="overlay-dialog-close"
          label={`Close ${heading}`} onClick={() => overlays.closeLayer(name)} />}
      </header>
      <div className="overlay-dialog-body">{isOpen ? children : null}</div>
    </div>
  </div>;
  return overlays.isNested(name) ? createPortal(shell, document.body) : shell;
}
