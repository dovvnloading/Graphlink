import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

/**
 * The SPA overlay system (Qt-removal plan R2) - the OverlayManager contract
 * from the Qt app, natively trivial in one DOM:
 *
 * - one registry of named surfaces, two tiers: POPOVER (anchored, light)
 *   and DIALOG (centered, scrimmed);
 * - single-open across BOTH tiers - opening anything closes whatever else
 *   is open (the audit-B1 policy, carried over verbatim);
 * - Escape closes the open surface, wherever focus lives - one document
 *   listener, no titleChanged relays, no ShortcutOverride interception:
 *   the entire class of Qt/Chromium keyboard-arbitration workarounds
 *   (page-side sentinel scripts, app-level event filters) ceases to exist;
 * - outside-click dismisses popovers (the click still lands - light-dismiss
 *   contract); dialogs dismiss via scrim click, close button, or Escape;
 * - dialogs get a focus trap (Tab cycles inside, focus restored on close);
 * - chip active-state is a context read of REAL open state, never latched
 *   click state (audit B6).
 */

export type OverlayTier = "popover" | "dialog";

interface OverlayContextValue {
  openSurface: string | null;
  open: (name: string, tier: OverlayTier) => void;
  close: () => void;
  toggle: (name: string, tier: OverlayTier) => void;
  isOpen: (name: string) => boolean;
  registerSurfaceElement: (name: string, element: HTMLElement | null) => void;
}

const OverlayContext = createContext<OverlayContextValue | null>(null);

export function useOverlays(): OverlayContextValue {
  const context = useContext(OverlayContext);
  if (!context) throw new Error("useOverlays requires an <OverlayProvider>");
  return context;
}

export function OverlayProvider({ children }: { children: ReactNode }) {
  const [openSurface, setOpenSurface] = useState<string | null>(null);
  const [openTier, setOpenTier] = useState<OverlayTier | null>(null);
  const surfaceElements = useRef(new Map<string, HTMLElement>());
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  const open = useCallback((name: string, tier: OverlayTier) => {
    restoreFocusRef.current = document.activeElement as HTMLElement | null;
    setOpenSurface(name);
    setOpenTier(tier);
  }, []);

  const close = useCallback(() => {
    setOpenSurface(null);
    setOpenTier(null);
    const restore = restoreFocusRef.current;
    restoreFocusRef.current = null;
    // Restore focus to the opener - half of the dialog focus contract.
    if (restore && document.contains(restore)) restore.focus();
  }, []);

  const toggle = useCallback(
    (name: string, tier: OverlayTier) => {
      if (openSurface === name) close();
      else open(name, tier);
    },
    [close, open, openSurface],
  );

  const isOpen = useCallback((name: string) => openSurface === name, [openSurface]);

  const registerSurfaceElement = useCallback((name: string, element: HTMLElement | null) => {
    if (element) surfaceElements.current.set(name, element);
    else surfaceElements.current.delete(name);
  }, []);

  // Escape closes the open surface - document capture phase so it wins even
  // with focus inside inputs.
  useEffect(() => {
    if (openSurface === null) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        close();
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [openSurface, close]);

  // Outside-click light-dismiss for POPOVERS only. pointerdown, not click:
  // the press dismisses AND still lands on what was pressed. The surface's
  // own trigger (data-overlay-trigger={name}) is exempt - otherwise its
  // pointerdown would light-dismiss and its click would immediately reopen,
  // making the chip a can't-close toggle.
  useEffect(() => {
    if (openSurface === null || openTier !== "popover") return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as HTMLElement;
      const trigger = target.closest?.("[data-overlay-trigger]");
      if (trigger?.getAttribute("data-overlay-trigger") === openSurface) return;
      const element = surfaceElements.current.get(openSurface);
      if (element && !element.contains(event.target as Node)) close();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [openSurface, openTier, close]);

  const value = useMemo(
    () => ({ openSurface, open, close, toggle, isOpen, registerSurfaceElement }),
    [openSurface, open, close, toggle, isOpen, registerSurfaceElement],
  );

  return <OverlayContext.Provider value={value}>{children}</OverlayContext.Provider>;
}

/** Anchored light surface. Render it where it should appear (CSS positions
 * it); it mounts only while open. The opener button stays outside. */
export function Popover({
  name,
  className,
  children,
}: {
  name: string;
  className?: string;
  children: ReactNode;
}) {
  const overlays = useOverlays();
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    overlays.registerSurfaceElement(name, ref.current);
    return () => overlays.registerSurfaceElement(name, null);
  });

  if (!overlays.isOpen(name)) return null;
  return (
    <div ref={ref} role="dialog" aria-modal="false" className={`overlay-popover ${className ?? ""}`}>
      {children}
    </div>
  );
}

const FOCUSABLE =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/** Centered modal surface with scrim + focus trap + mandatory titled header
 * and close button (the audit-B5 rule: every dialog is closeable on sight). */
export function Dialog({
  name,
  title,
  className,
  children,
}: {
  name: string;
  title: string;
  className?: string;
  children: ReactNode;
}) {
  const overlays = useOverlays();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const isOpen = overlays.isOpen(name);

  useEffect(() => {
    overlays.registerSurfaceElement(name, panelRef.current);
    return () => overlays.registerSurfaceElement(name, null);
  });

  // Focus trap: focus the panel on open; Tab cycles within it.
  useEffect(() => {
    if (!isOpen) return;
    const panel = panelRef.current;
    if (!panel) return;
    const first = panel.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel).focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const focusables = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (focusables.length === 0) return;
      const firstEl = focusables[0];
      const lastEl = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === firstEl) {
        event.preventDefault();
        lastEl.focus();
      } else if (!event.shiftKey && document.activeElement === lastEl) {
        event.preventDefault();
        firstEl.focus();
      }
    };
    panel.addEventListener("keydown", onKeyDown);
    return () => panel.removeEventListener("keydown", onKeyDown);
  }, [isOpen]);

  if (!isOpen) return null;
  return (
    <div className="overlay-scrim" onPointerDown={(e) => e.target === e.currentTarget && overlays.close()}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={`overlay-dialog ${className ?? ""}`}
      >
        <header className="overlay-dialog-header">
          <span className="overlay-dialog-title">{title}</span>
          <button
            type="button"
            className="overlay-dialog-close"
            aria-label={`Close ${title}`}
            onClick={overlays.close}
          >
            ×
          </button>
        </header>
        <div className="overlay-dialog-body">{children}</div>
      </div>
    </div>
  );
}
