import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";

/**
 * The SPA overlay system (Qt-removal plan R2) - the OverlayManager contract
 * from the Qt app, natively trivial in one DOM:
 *
 * - one registry of named surfaces, two tiers: POPOVER (anchored, light)
 *   and DIALOG (centered, scrimmed);
 * - single-open across BOTH tiers - opening anything closes whatever else
 *   is open (the audit-B1 policy, carried over verbatim);
 * - Escape closes the open surface, wherever focus lives, UNLESS something
 *   more specific already claimed it (event.preventDefault(), the standard
 *   "I'm handling this" signal - an inline editor reverting its own edit,
 *   say) - one document listener, no titleChanged relays, no
 *   ShortcutOverride interception: the entire class of Qt/Chromium
 *   keyboard-arbitration workarounds (page-side sentinel scripts, app-level
 *   event filters) ceases to exist;
 * - outside-click dismisses popovers (the click still lands - light-dismiss
 *   contract); dialogs dismiss via scrim click, close button, or Escape;
 * - dialogs get a focus trap (Tab cycles inside, focus restored on close),
 *   hardened against a single leak permanently defeating it (R8a finding
 *   #17 - see Dialog's own comment);
 * - chip active-state is a context read of REAL open state, never latched
 *   click state (audit B6).
 */

export type OverlayTier = "popover" | "dialog";

export interface OverlayContextValue {
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

  // Escape closes the open surface. R8a (UI/UX issue list finding #16):
  // this used to run on document's CAPTURE phase and call
  // stopPropagation() unconditionally - which wins even with focus inside
  // an input, but wins TOO completely: capture fires before the event ever
  // reaches React's own bubble-phase dispatch (React delegates listeners to
  // the root container, itself a document descendant), so stopping it here
  // meant an inline editor's own Escape handler - Chat Library's rename
  // input, a Note's or a Frame/Container's inline label editor - could
  // never run at all while any overlay was open, even one unrelated to it
  // (a note being edited on the canvas behind an open Pins popover, say).
  // Escape looked like it did nothing, or worse, closed the wrong thing.
  //
  // Now on the BUBBLE phase instead, and gated on event.defaultPrevented:
  // an inner handler that wants to claim Escape calls
  // event.preventDefault() (the standard way to say "I'm handling this"),
  // which this listener checks for since it fires last, after the event
  // has already bubbled through every more-specific handler beneath it.
  // Handlers that don't call preventDefault (there's no browser default
  // action to prevent for a bare Escape - it's purely a convention this
  // provider now depends on) are unaffected: Escape still closes the
  // overlay exactly as before. NodeMenu.tsx's own Escape handling is a
  // separate, deliberately different pattern (capture phase + an
  // unconditional stopPropagation of its own) and is unaffected by this
  // change - it already wins the race against this listener regardless of
  // which phase this one runs on, since capture always fires first.
  useEffect(() => {
    if (openSurface === null) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) {
        close();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
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
  label,
  className,
  children,
}: {
  name: string;
  label: string;
  className?: string;
  children: ReactNode;
}) {
  const overlays = useOverlays();
  const ref = useRef<HTMLDivElement | null>(null);
  const isOpen = overlays.isOpen(name);

  useEffect(() => {
    overlays.registerSurfaceElement(name, ref.current);
    return () => overlays.registerSurfaceElement(name, null);
  });

  // R8a (UI/UX issue list finding #19): opening a popover via keyboard
  // (Enter on its trigger button) left focus sitting on the trigger - the
  // panel's own controls were reachable only by continuing to Tab through
  // whatever else sits between the trigger and the panel in DOM order (the
  // rest of the app bar, for View/Plugins/Pins). Focus RESTORATION on
  // close is already handled generically by open()/close() above for every
  // overlay tier; moving focus IN on open is the other half, specific to
  // this component (Dialog already does it in its own effect).
  useEffect(() => {
    if (!isOpen) return;
    const panel = ref.current;
    if (!panel) return;
    const first = panel.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel).focus();
  }, [isOpen]);

  if (!isOpen) return null;
  return (
    <div
      ref={ref}
      role="dialog"
      aria-modal="false"
      aria-label={label}
      tabIndex={-1}
      className={`overlay-popover ${className ?? ""}`}
    >
      {children}
    </div>
  );
}

// R8a (UI/UX issue list finding #17): the original selector didn't exclude
// [disabled] - a disabled button/input/select/textarea is real DOM content
// that querySelectorAll happily returns, but the BROWSER skips it on Tab
// (disabled elements are never part of the native tab order). Three of
// Settings' five sections end in a primary button disabled in its default
// state (IntegrationsPage/ApiProviderPage/OllamaPage - each gated on an
// empty draft field), so "the last focusable element" as this trap
// computed it was frequently a button Tab could never actually land on -
// activeElement === lastEl was never true, the wrap-around branch below
// never ran, and focus walked straight out of the dialog onto the app
// behind the scrim.
const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Deliberately NOT also filtering on el.offsetParent !== null (an
// "is this actually rendered, not display:none'd by some ancestor" check
// the audit's own fuller fix suggests, for a hidden tab section / a step
// not yet reached). Two reasons: this app's own dialogs don't have that
// shape today - every multi-section dialog audited (Settings included)
// switches sections by conditionally RENDERING the active one, not by
// CSS-hiding inactive ones while leaving them mounted, so there is no
// concrete instance of this ever mattering to check against; and jsdom
// (this file's own test environment) does not implement layout at all,
// so offsetParent reads null unconditionally regardless of real
// visibility - filtering on it would make every focusable in every test
// vanish, not just genuinely hidden ones, which is worse than not
// checking it. The Tab-gated focusin backstop below still catches a
// focus escape from THIS cause exactly the same as any other - it does
// not depend on knowing why the trap's own boundary was wrong, only that
// a Tab press left focus outside the panel.

/**
 * R8a (finding #17): a belt-and-suspenders backstop for a Tab-cycling focus
 * trap. The primary trap (Dialog's own effect below, or a standalone one
 * like CodeExecutionApprovalPanel.tsx's) is scoped to the panel itself - it
 * only ever runs for a keydown that reaches an element ALREADY inside the
 * panel, so it has no way to pull focus back once it has already escaped
 * (a disabled trailing button proved reachable; some future change to a
 * panel's own content - a hidden tab section, say - could reopen it a
 * different way). A `focusin` listener at `document` sees focus AFTER it
 * lands anywhere, regardless of how it got there or why the primary trap's
 * boundary was wrong, and is the one place that can catch an escape the
 * trap above missed.
 *
 * Exported so this exact, easy-to-get-wrong logic is never hand-written a
 * third time - it already has two real bugs behind it, both found the hard
 * way (a failing test / a live stack overflow), not by inspection:
 *
 * - An UNGATED version (redirect whenever focus lands outside the panel,
 *   full stop) fights close()'s own legitimate focus restoration:
 *   overlays.tsx's close() restores focus to the ORIGINAL trigger element -
 *   by definition outside the panel - SYNCHRONOUSLY, in the same tick that
 *   sets openSurface to null, before React has re-rendered and before this
 *   effect's own cleanup has run. An ungated listener is still attached at
 *   that exact moment and yanks focus back into a dialog mid-close.
 * - The SAME ungated version also infinite-loops the moment more than one
 *   trapped panel is open at once (CodeExecutionApprovalPanel.tsx
 *   deliberately supports this - two pending approvals on different
 *   nodes): each panel's own unconditional listener sees the OTHER panel's
 *   redirect as ITS OWN escape and redirects back, forever.
 *
 * Gating on "was Tab literally the immediately-preceding key" (reset by any
 * OTHER key, including Escape, and by any pointerdown - closes the gap a
 * mouse click on a close button would otherwise leave) fixes both: it
 * excludes every programmatic .focus() call by construction (close()'s
 * restore included), and each panel's own flag is consumed by its own
 * FIRST redirect, bounding any cross-panel ping-pong instead of looping.
 */
export function useFocusEscapeBackstop(
  panelRef: RefObject<HTMLElement | null>,
  active: boolean,
  focusableSelector: string,
) {
  useEffect(() => {
    if (!active) return;
    const panel = panelRef.current;
    if (!panel) return;
    let lastKeyWasTab = false;
    const onKeyDownCapture = (event: KeyboardEvent) => {
      lastKeyWasTab = event.key === "Tab";
    };
    const onPointerDown = () => {
      lastKeyWasTab = false;
    };
    const onFocusIn = (event: FocusEvent) => {
      if (!lastKeyWasTab) return;
      lastKeyWasTab = false;
      if (panel.contains(event.target as Node)) return;
      const focusables = [...panel.querySelectorAll<HTMLElement>(focusableSelector)];
      (focusables[0] ?? panel).focus();
    };
    document.addEventListener("keydown", onKeyDownCapture, true);
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("keydown", onKeyDownCapture, true);
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("focusin", onFocusIn);
    };
  }, [active, panelRef, focusableSelector]);
}

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
    const focusableIn = () => [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)];
    const first = focusableIn()[0];
    (first ?? panel).focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const focusables = focusableIn();
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

  useFocusEscapeBackstop(panelRef, isOpen, FOCUSABLE);

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
