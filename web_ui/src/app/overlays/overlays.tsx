import {
  createContext,
  useId,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { Icon } from "../ui/icons/Icon";
import "../ui/overlay.css";

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
type Layer = { name: string; tier: OverlayTier; parent: string | null; trigger: HTMLElement | null; state: "open" | "closing" };

export interface OverlayContextValue {
  openSurface: string | null;
  open: (name: string, tier: OverlayTier) => void;
  openNested: (name: string, tier: OverlayTier, trigger?: HTMLElement | null) => void;
  close: () => void;
  closeLayer: (name: string) => void;
  toggle: (name: string, tier: OverlayTier) => void;
  isOpen: (name: string) => boolean;
  isClosing: (name: string) => boolean;
  isNested: (name: string) => boolean;
  isTopmost: (name: string) => boolean;
  registerSurfaceElement: (name: string, element: HTMLElement | null) => void;
}

const OverlayContext = createContext<OverlayContextValue | null>(null);
const EXIT_MS = 120;

export function useOverlays(): OverlayContextValue {
  const context = useContext(OverlayContext);
  if (!context) throw new Error("useOverlays requires an <OverlayProvider>");
  return context;
}

export function OverlayProvider({ children }: { children: ReactNode }) {
  const [layers, setLayers] = useState<Layer[]>([]);
  const layersRef = useRef<Layer[]>([]);
  const surfaceElements = useRef(new Map<string, HTMLElement>());
  const exitTimers = useRef(new Map<string, number>());

  const publish = useCallback((next: Layer[]) => {
    layersRef.current = next;
    setLayers(next);
  }, []);

  const scheduleRemoval = useCallback((name: string) => {
    const previous = exitTimers.current.get(name);
    if (previous !== undefined) window.clearTimeout(previous);
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) {
      publish(layersRef.current.filter((layer) => !(layer.name === name && layer.state === "closing")));
      return;
    }
    const timer = window.setTimeout(() => {
      exitTimers.current.delete(name);
      publish(layersRef.current.filter((layer) => !(layer.name === name && layer.state === "closing")));
    }, EXIT_MS);
    exitTimers.current.set(name, timer);
  }, [publish]);

  useEffect(() => () => {
    for (const timer of exitTimers.current.values()) window.clearTimeout(timer);
    exitTimers.current.clear();
  }, []);

  const dismiss = useCallback((name?: string) => {
    const active = layersRef.current.filter((layer) => layer.state === "open");
    const index = name ? active.findIndex((layer) => layer.name === name) : active.length - 1;
    if (index < 0) return;
    const closing = active.slice(index);
    const names = new Set(closing.map((layer) => layer.name));
    const trigger = active[index].trigger;
    publish(layersRef.current.map((layer) => names.has(layer.name) ? { ...layer, state: "closing" as const } : layer));
    for (const layer of closing) scheduleRemoval(layer.name);
    if (trigger && document.contains(trigger)) trigger.focus();
  }, [publish, scheduleRemoval]);

  const close = useCallback(() => dismiss(), [dismiss]);
  const closeLayer = useCallback((name: string) => dismiss(name), [dismiss]);

  const activate = useCallback((name: string, tier: OverlayTier, nested: boolean, opener?: HTMLElement | null) => {
    const active = layersRef.current.filter((layer) => layer.state === "open");
    if (active.some((layer) => layer.name === name)) return;
    const root = active[0];
    const focused = document.activeElement;
    const trigger = opener ?? (focused instanceof HTMLElement ? focused : null);
    const rootTrigger = root?.trigger ?? trigger;
    const parent = nested && root ? active[active.length - 1].name : null;
    const oldTimer = exitTimers.current.get(name);
    if (oldTimer !== undefined) window.clearTimeout(oldTimer);
    exitTimers.current.delete(name);
    const previous = layersRef.current.filter((layer) => layer.name !== name);
    const replaced = new Set((parent ? [] : active).map((layer) => layer.name));
    publish([
      ...previous.map((layer) => replaced.has(layer.name) ? { ...layer, state: "closing" as const } : layer),
      { name, tier, trigger: parent ? trigger : rootTrigger, parent, state: "open" },
    ]);
    for (const layer of active) if (replaced.has(layer.name)) scheduleRemoval(layer.name);
  }, [publish, scheduleRemoval]);

  const openNested = useCallback(
    (name: string, tier: OverlayTier, trigger?: HTMLElement | null) => activate(name, tier, true, trigger),
    [activate],
  );

  const open = useCallback((name: string, tier: OverlayTier) => {
    const active = layersRef.current.filter((layer) => layer.state === "open");
    const root = active[0];
    const focused = document.activeElement;
    // Existing child popovers/dialogs can nest without changing their call sites.
    const inside = root?.tier === "dialog" && active.some((layer) => {
      const panel = surfaceElements.current.get(layer.name);
      return panel && focused instanceof Node && panel.contains(focused);
    });
    activate(name, tier, Boolean(inside));
  }, [activate]);

  const toggle = useCallback((name: string, tier: OverlayTier) => {
    if (layersRef.current.some((layer) => layer.name === name && layer.state === "open")) dismiss(name);
    else open(name, tier);
  }, [dismiss, open]);

  const registerSurfaceElement = useCallback((name: string, element: HTMLElement | null) => {
    if (element) surfaceElements.current.set(name, element);
    else surfaceElements.current.delete(name);
  }, []);

  const active = layers.filter((layer) => layer.state === "open");
  const openSurface = active.find((layer) => layer.parent === null)?.name ?? null;
  const topmost = active[active.length - 1]?.name ?? null;
  const isOpen = useCallback((name: string) => layers.some((layer) =>
    layer.name === name && layer.state === "open"), [layers]);
  const isClosing = useCallback((name: string) => layers.some((layer) =>
    layer.name === name && layer.state === "closing"), [layers]);
  const isNested = useCallback((name: string) => layers.some((layer) =>
    layer.name === name && layer.parent !== null), [layers]);
  const isTopmost = useCallback((name: string) => name === topmost, [topmost]);

  // Escape is bubble-phase, so inline editors may claim it first.
  useEffect(() => {
    if (!topmost) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) close();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [topmost, close]);

  // Light dismiss only the uppermost popover. Its own trigger is exempt.
  useEffect(() => {
    const openLayers = layers.filter((layer) => layer.state === "open");
    const top = openLayers[openLayers.length - 1];
    if (!top || top.tier !== "popover") return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (top.trigger?.contains(target)) return;
      if (target instanceof Element &&
          target.closest("[data-overlay-trigger]")?.getAttribute("data-overlay-trigger") === top.name) return;
      const panel = surfaceElements.current.get(top.name);
      if (!panel?.contains(target)) close();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [layers, close]); // updates when the top changes

  const value = useMemo(
    () => ({ openSurface, open, openNested, close, closeLayer, toggle, isOpen, isClosing, isNested, isTopmost, registerSurfaceElement }),
    [openSurface, open, openNested, close, closeLayer, toggle, isOpen, isClosing, isNested, isTopmost, registerSurfaceElement],
  );
  return <OverlayContext.Provider value={value}>{children}</OverlayContext.Provider>;
}

/**
 * Standalone canvas nodes can be mounted outside the app shell (for example,
 * in a focused renderer or on a test rerender). Reuse the app's shared overlay
 * stack whenever it exists; only create a local one when no provider exists.
 */
export function OverlayProviderIfMissing({ children }: { children: ReactNode }) {
  const parent = useContext(OverlayContext);
  return parent ? <>{children}</> : <OverlayProvider>{children}</OverlayProvider>;
}

const ANCHOR_MARGIN = 8;
const ANCHOR_GAP = 6;

/**
 * R8a (UI/UX issue list finding #27): the anchored variant's placement
 * logic. View/Plugins/Pins used to render inside a wrapper div hard-
 * positioned at a fixed corner of the canvas (independent of which button
 * was actually clicked) - View and Plugins sat at byte-identical
 * coordinates despite being two different, adjacent toolbar buttons.
 *
 * Portaled to document.body (so `position: fixed` resolves against the
 * real viewport, the same reason NodeMenu.tsx does it) and positioned from
 * the trigger's own getBoundingClientRect(), the same anchoring contract
 * the Qt OverlayManager had. Opens below-left of the trigger by default,
 * flips above it if there isn't room below, and clamps horizontally so it
 * never runs off either edge - mirroring NodeMenu's own flip-and-clamp
 * effect (same problem, a click point there vs. a trigger rect here).
 *
 * The composer's model and reasoning pickers use this placement to open by
 * their controls in both the inline desktop footer and its narrow layout.
 */
function useAnchoredPlacement(
  panelRef: RefObject<HTMLElement | null>,
  triggerName: string,
  isOpen: boolean,
) {
  const [placement, setPlacement] = useState<{ top: number; left: number } | null>(null);

  // One pass, not a render-then-correct two-step: .overlay-popover's width/
  // height come from its CSS (padding, min/max-width, content) and don't
  // depend on `position` being fixed vs. the browser's static default, so
  // the panel's OWN rect is already meaningful on the very first layout
  // pass after it mounts - there is nothing to flip/clamp against on a
  // later pass that isn't already available on this one.
  //
  // No explicit "reset to null while closed" branch: Popover's own
  // `if (!isOpen) return null` already unmounts the panel entirely, so a
  // stale placement value sitting in state while closed is never rendered
  // - the next open recomputes it before anything reads it again.
  useLayoutEffect(() => {
    if (!isOpen) return;
    const trigger = document.querySelector<HTMLElement>(`[data-overlay-trigger="${triggerName}"]`);
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

    if (!placement || placement.top !== top || placement.left !== left) {
      setPlacement({ top, left });
    }
    // Deliberately excludes `placement` - it is read only to bail out once
    // the computed value stops changing, so depending on it would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, triggerName, panelRef]);

  return placement;
}

/** Anchored light surface. Render it where it should appear (CSS positions
 * it, unless `anchored` is set - see useAnchoredPlacement above); it mounts
 * only while open. The opener button stays outside. */

export function Popover({
  name, label, className, anchored, children,
}: {
  name: string; label: string; className?: string; anchored?: boolean; children: ReactNode;
}) {
  const overlays = useOverlays();
  const ref = useRef<HTMLDivElement | null>(null);
  const isOpen = overlays.isOpen(name);
  const closing = overlays.isClosing(name);
  const placement = useAnchoredPlacement(ref, name, isOpen && !!anchored);

  useEffect(() => {
    if (!isOpen) return;
    overlays.registerSurfaceElement(name, ref.current);
    return () => overlays.registerSurfaceElement(name, null);
  }, [isOpen, name, overlays.registerSurfaceElement]);

  useEffect(() => {
    if (!isOpen) return;
    const panel = ref.current;
    if (panel) (panel.querySelector<HTMLElement>(FOCUSABLE) ?? panel).focus();
  }, [isOpen]);

  if (!isOpen && !closing) return null;
  const panel = (
    <div
      ref={ref} role="dialog" aria-modal="false" aria-label={label}
      aria-hidden={closing ? true : undefined} tabIndex={-1}
      data-state={closing ? "closing" : "open"}
      data-nested={overlays.isNested(name) ? "" : undefined}
      className={`overlay-popover ${anchored ? "overlay-popover-anchored" : ""} ${className ?? ""}`}
      style={anchored && placement ? { top: placement.top, left: placement.left } : undefined}
    >
      {isOpen ? children : null}
    </div>
  );
  return anchored ? createPortal(panel, document.body) : panel;
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


/** A scrolling dialog with an optional sticky footer and nested-layer support. */
export function Dialog({
  name, title, description, size = "md", footer, status, className, children,
}: {
  name: string; title: string; description?: string; size?: "sm" | "md" | "lg" | "xl";
  footer?: ReactNode; status?: ReactNode; className?: string; children: ReactNode;
}) {
  const overlays = useOverlays();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const titleRef = useRef<HTMLSpanElement | null>(null);
  const titleId = useId();
  const descriptionId = useId();
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
    (preferred ?? field ?? titleRef.current)?.focus();
  }, [isOpen]);

  // Only the top dialog traps Tab; its portaled nested layers may take focus.
  useEffect(() => {
    if (!isOpen || !topmost) return;
    const panel = panelRef.current;
    if (!panel) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const focusables = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!focusables.length) {
        event.preventDefault();
        titleRef.current?.focus();
        return;
      }
      const first = focusables[0], last = focusables[focusables.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === titleRef.current)) {
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
  const shell = (
    <div className="overlay-scrim" data-state={closing ? "closing" : "open"}
      data-nested={overlays.isNested(name) ? "" : undefined}
      aria-hidden={closing ? true : undefined}
      onPointerDown={(event) => {
        if (isOpen && topmost && event.target === event.currentTarget) overlays.closeLayer(name);
      }}
    >
      <div ref={panelRef} role="dialog" aria-modal="true"
        aria-labelledby={titleId} aria-describedby={description ? descriptionId : undefined}
        data-state={closing ? "closing" : "open"} data-size={size} tabIndex={-1}
        className={`overlay-dialog ${className ?? ""}`}
      >
        <header className="overlay-dialog-header">
          <div className="overlay-dialog-heading">
            <span ref={titleRef} id={titleId} tabIndex={-1} role="heading" aria-level={2}
              className="overlay-dialog-title">{title}</span>
            {description && <p id={descriptionId} className="overlay-dialog-description">{description}</p>}
          </div>
          {isOpen && <button type="button" className="overlay-dialog-close"
            aria-label={`Close ${title}`} onClick={() => overlays.closeLayer(name)}>
            <Icon name="close" />
          </button>}
        </header>
        <div className="overlay-dialog-body">{isOpen ? children : null}</div>
        {isOpen && (footer !== undefined || status !== undefined) && (
          <footer className="overlay-dialog-footer">
            <span className="overlay-dialog-status">{status}</span>
            <div className="overlay-dialog-actions">{footer}</div>
          </footer>
        )}
      </div>
    </div>
  );
  return overlays.isNested(name) ? createPortal(shell, document.body) : shell;
}
