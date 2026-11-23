import {
  Children, cloneElement, createContext, isValidElement, useCallback, useContext,
  useEffect, useId, useLayoutEffect, useMemo, useRef, useState,
  type ButtonHTMLAttributes, type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent, type ReactNode, type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { OverlayProviderIfMissing, useOverlays } from "../overlays/overlays";
import { clampMenuPosition, clampSubMenuPosition, type MenuPosition } from "../canvas/menuPosition";
import { isMenuKey } from "../canvas/keyboardContextMenu";
import { Icon } from "./icons/Icon";
import type { IconName } from "./icons/registry";
import { Kbd } from "./Kbd";
import "./menu.css";

const ITEM_SELECTOR = [
  '[role="menuitem"]:not([disabled]):not([aria-disabled="true"])',
  '[role="menuitemcheckbox"]:not([disabled]):not([aria-disabled="true"])',
  '[role="menuitemradio"]:not([disabled]):not([aria-disabled="true"])',
].join(", ");

type MenuContextValue = {
  dismiss: () => void;
  nested: boolean;
  submenuPanels: RefObject<Set<HTMLElement>>;
  submenuClosers: RefObject<Array<() => void>>;
};
const MenuContext = createContext<MenuContextValue | null>(null);

function enabledItems(panel: HTMLElement): HTMLElement[] {
  return Array.from(panel.querySelectorAll<HTMLElement>(ITEM_SELECTOR))
    .filter((item) => !item.closest('[hidden], [aria-hidden="true"]'));
}

function itemLabel(item: HTMLElement): string {
  return (item.getAttribute("data-menu-label") ?? item.getAttribute("aria-label") ?? item.textContent ?? "")
    .trim().toLocaleLowerCase();
}

/** Native capture listener works for the kit and legacy raw role=menuitem buttons.
 * The active panel is re-queried on every key, including after inline submenus change. */
function useMenuKeyboard(
  panelRef: RefObject<HTMLElement | null>,
  enabled: boolean,
  onEscape: () => void,
  onLeft?: () => void,
) {
  const typeAhead = useRef({ text: "", time: 0 });
  useEffect(() => {
    if (!enabled) return;
    const onKeyDown = (event: KeyboardEvent) => {
      const panel = panelRef.current;
      const focused = document.activeElement;
      if (!panel || !(focused instanceof HTMLElement) || !panel.contains(focused)) return;
      const items = enabledItems(panel);
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onEscape();
        return;
      }
      if (event.key === "ArrowLeft" && onLeft) {
        event.preventDefault();
        event.stopPropagation();
        onLeft();
        return;
      }
      if (event.key === "Tab") {
        event.preventDefault();
        event.stopPropagation();
        onEscape();
        return;
      }
      if (!items.length) return;
      const index = items.indexOf(focused);
      let next = -1;
      if (event.key === "ArrowDown") next = (index + 1) % items.length;
      else if (event.key === "ArrowUp") next = (index - 1 + items.length) % items.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = items.length - 1;
      if (next >= 0) {
        event.preventDefault();
        event.stopPropagation();
        items[next].focus();
        items[next].scrollIntoView?.({ block: "nearest" });
        return;
      }
      if ((event.key === "Enter" || event.key === " " || event.key === "Spacebar") &&
          focused.matches(ITEM_SELECTOR) && focused.getAttribute("aria-haspopup") !== "menu") {
        event.preventDefault();
        event.stopPropagation();
        focused.click();
        return;
      }
      if (event.key.length !== 1 || !/\S/.test(event.key) ||
          event.ctrlKey || event.altKey || event.metaKey) return;
      const letter = event.key.toLocaleLowerCase();
      const now = Date.now();
      const prefix = (now - typeAhead.current.time < 700 ? typeAhead.current.text : "") + letter;
      const find = (text: string) => {
        for (let n = 1; n <= items.length; n++) {
          const candidate = items[(index + n + items.length) % items.length];
          if (itemLabel(candidate).startsWith(text)) return candidate;
        }
        return null;
      };
      const match = find(prefix) ?? find(letter);
      typeAhead.current = { text: match && itemLabel(match).startsWith(prefix) ? prefix : letter, time: now };
      if (match) {
        event.preventDefault();
        event.stopPropagation();
        match.focus();
        match.scrollIntoView?.({ block: "nearest" });
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [panelRef, enabled, onEscape, onLeft]);
}

export interface MenuItemProps {
  label: string;
  icon?: IconName;
  shortcut?: string;
  description?: string;
  checked?: boolean;
  kind?: "checkbox" | "radio";
  role?: "menuitem" | "menuitemcheckbox" | "menuitemradio";
  destructive?: boolean;
  disabled?: boolean;
  disabledReason?: string;
  onSelect?: () => void;
  onClick?: (event: ReactMouseEvent<HTMLButtonElement>) => void;
  className?: string;
}

export function MenuItem({
  label, icon, shortcut, description, checked, kind, role, destructive = false,
  disabled = false, disabledReason, onSelect, onClick, className,
}: MenuItemProps) {
  const menu = useContext(MenuContext);
  const reasonId = useId();
  const itemRole = role ?? (kind === "radio" ? "menuitemradio" : checked === undefined ? "menuitem" : "menuitemcheckbox");
  const isChoice = itemRole === "menuitemcheckbox" || itemRole === "menuitemradio";
  return (
    <>
      <button
        type="button"
        role={itemRole}
        aria-checked={isChoice ? Boolean(checked) : undefined}
        aria-label={label}
        data-menu-label={label}
        data-destructive={destructive ? "true" : undefined}
        className={["gl-menu-item", className].filter(Boolean).join(" ")}
        tabIndex={-1}
        disabled={disabled}
        aria-describedby={disabled && disabledReason ? reasonId : undefined}
        onClick={(event) => {
          if (disabled) return;
          onClick?.(event);
          if (event.defaultPrevented) return;
          onSelect?.();
          menu?.dismiss();
        }}
      >
        <span className="gl-menu-item-icon">
          {(isChoice && checked) || destructive
            ? <Icon name={destructive ? "trash" : "check"} size="sm" />
            : icon ? <Icon name={icon} size="sm" /> : null}
        </span>
        <span className="gl-menu-item-body">
          <span className="gl-menu-item-label">{label}</span>
          {description && <span className="gl-menu-item-description">{description}</span>}
        </span>
        {shortcut && <Kbd shortcut={shortcut} className="gl-menu-item-shortcut" />}
      </button>
      {disabled && disabledReason && <span id={reasonId} className="gl-menu-disabled-reason">{disabledReason}</span>}
    </>
  );
}

export function MenuSeparator() {
  return <div className="gl-menu-separator" role="separator" />;
}

export function MenuGroup({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  return (
    <div className="gl-menu-group" role="group" aria-labelledby={id}>
      <span id={id} className="gl-menu-group-label">{label}</span>
      {children}
    </div>
  );
}

/** Destructive entries are never interleaved with ordinary actions. This also
 * lifts destructive entries out of groups, leaving their original labels intact. */
function partitionItems(children: ReactNode): { regular: ReactNode[]; destructive: ReactNode[] } {
  const regular: ReactNode[] = [];
  const destructive: ReactNode[] = [];
  for (const child of Children.toArray(children)) {
    if (isValidElement<MenuItemProps>(child) && child.type === MenuItem && child.props.destructive) {
      destructive.push(child);
    } else if (isValidElement<{ label: string; children: ReactNode }>(child) && child.type === MenuGroup) {
      const nested = partitionItems(child.props.children);
      if (nested.regular.length) regular.push(cloneElement(child, { children: nested.regular }));
      destructive.push(...nested.destructive);
    } else {
      regular.push(child);
    }
  }
  return { regular, destructive };
}

export interface MenuProps {
  position: MenuPosition;
  onClose: () => void;
  children: ReactNode;
  ariaLabel?: string;
  className?: string;
  id?: string;
  ignoreRef?: RefObject<HTMLElement | null>;
  /** Explicit focus origin for a pointer-triggered menu. */
  restoreFocusRef?: RefObject<HTMLElement | null>;
}

export function Menu(props: MenuProps) {
  return <OverlayProviderIfMissing><MenuSurface {...props} /></OverlayProviderIfMissing>;
}

function MenuSurface({
  position, onClose, children, ariaLabel, className, id, ignoreRef, restoreFocusRef,
}: MenuProps) {
  const overlays = useOverlays();
  const menuId = useId();
  const layerName = "menu-" + menuId;
  const panelRef = useRef<HTMLDivElement | null>(null);
  const submenuPanels = useRef(new Set<HTMLElement>());
  const submenuClosers = useRef<Array<() => void>>([]);
  const focusOrigin = useRef<HTMLElement | null>(null);
  const [placement, setPlacement] = useState(position);
  const closeTop = useCallback(() => {
    const last = submenuClosers.current[submenuClosers.current.length - 1];
    if (last) last();
    else onClose();
  }, [onClose]);
  const context = useMemo<MenuContextValue>(() => ({
    dismiss: onClose, nested: overlays.isNested(layerName), submenuPanels, submenuClosers,
  }), [onClose, overlays, layerName]);
  const { regular, destructive } = partitionItems(children);

  useMenuKeyboard(panelRef, true, closeTop);

  useLayoutEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    const next = clampMenuPosition({ x: position.x, y: position.y }, panel.getBoundingClientRect());
    setPlacement((prev) => prev.x === next.x && prev.y === next.y ? prev : next);
  }, [position.x, position.y]);

  useEffect(() => {
    const trigger = restoreFocusRef?.current ??
      (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    focusOrigin.current = trigger;
    const first = panelRef.current && enabledItems(panelRef.current)[0];
    first?.focus();
    return () => { if (trigger?.isConnected) trigger.focus(); };
  }, [restoreFocusRef]);

  useEffect(() => {
    overlays.openNested(layerName, "popover", focusOrigin.current);
    overlays.registerSurfaceElement(layerName, panelRef.current);
    return () => {
      overlays.registerSurfaceElement(layerName, null);
      overlays.closeLayer(layerName);
    };
  }, [layerName, overlays.openNested, overlays.closeLayer, overlays.registerSurfaceElement]);

  useEffect(() => {
    const trigger = focusOrigin.current;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (panelRef.current?.contains(target)) return;
      if (Array.from(submenuPanels.current).some((panel) => panel.contains(target))) return;
      if (ignoreRef?.current?.contains(target)) return;
      // A node's own kebab click closes its menu rather than reopening it.
      if (trigger?.matches(".node-card-menu-trigger") && trigger.contains(target)) return;
      onClose();
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [ignoreRef, onClose]);

  // When a submenu has focus, its own keyboard handler owns Escape/ArrowLeft.
  // The root handler only runs for focus inside this panel.
  return createPortal(
    <MenuContext.Provider value={context}>
      <div ref={panelRef} id={id} role="menu" aria-label={ariaLabel} tabIndex={-1}
        className={["gl-menu", className].filter(Boolean).join(" ")}
        data-nested={overlays.isNested(layerName) ? "" : undefined}
        style={{ position: "fixed", left: placement.x, top: placement.y }}>
        {regular}
        {destructive.length > 0 && (
          <>
            {regular.length > 0 && <MenuSeparator />}
            <div className="gl-menu-destructive-group" role="group" aria-label="Destructive actions">
              {destructive}
            </div>
          </>
        )}
      </div>
    </MenuContext.Provider>,
    document.body,
  );
}

export interface SubMenuProps {
  label: string;
  children: ReactNode;
  icon?: IconName;
  disabled?: boolean;
  className?: string;
}

export function SubMenu({ label, children, icon, disabled = false, className }: SubMenuProps) {
  const parent = useContext(MenuContext);
  const id = useId();
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const intentTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const keyboardOpen = useRef(false);
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState({ x: 0, y: 0 });
  const cancelIntent = useCallback(() => {
    if (intentTimer.current !== null) clearTimeout(intentTimer.current);
    intentTimer.current = null;
  }, []);
  const close = useCallback(() => {
    cancelIntent();
    setOpen(false);
    triggerRef.current?.focus();
  }, [cancelIntent]);
  const openPanel = useCallback((fromKeyboard: boolean) => {
    cancelIntent();
    keyboardOpen.current = fromKeyboard;
    setOpen(true);
  }, [cancelIntent]);
  const scheduleOpen = () => {
    if (disabled || open) return;
    cancelIntent();
    intentTimer.current = setTimeout(() => openPanel(false), 150);
  };
  const scheduleClose = () => {
    cancelIntent();
    intentTimer.current = setTimeout(() => setOpen(false), 150);
  };
  useEffect(() => () => cancelIntent(), [cancelIntent]);
  useLayoutEffect(() => {
    if (!open || !triggerRef.current || !panelRef.current) return;
    const next = clampSubMenuPosition(
      triggerRef.current.getBoundingClientRect(),
      panelRef.current.getBoundingClientRect(),
    );
    setPlacement((prev) => prev.x === next.x && prev.y === next.y ? prev : next);
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    if (panel) parent?.submenuPanels.current.add(panel);
    parent?.submenuClosers.current.push(close);
    if (keyboardOpen.current && panel) enabledItems(panel)[0]?.focus();
    return () => {
      if (panel) parent?.submenuPanels.current.delete(panel);
      if (parent) {
        const stack = parent.submenuClosers.current;
        const index = stack.indexOf(close);
        if (index >= 0) stack.splice(index, 1);
      }
    };
  }, [open, parent, close]);
  useMenuKeyboard(panelRef, open, close, close);

  const { regular, destructive } = partitionItems(children);
  return (
    <div className={["gl-submenu", className].filter(Boolean).join(" ")}
      onMouseEnter={cancelIntent} onMouseLeave={scheduleClose}>
      <button ref={triggerRef} type="button" role="menuitem" tabIndex={-1}
        className="gl-menu-item gl-submenu-trigger" aria-label={label}
        data-menu-label={label} disabled={disabled}
        aria-haspopup="menu" aria-controls={open ? id : undefined}
        aria-expanded={open}
        onMouseEnter={scheduleOpen}
        onKeyDown={(event: ReactKeyboardEvent<HTMLButtonElement>) => {
          if (event.key === "ArrowRight" || event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            event.stopPropagation();
            if (!disabled) openPanel(true);
          }
          if (event.key === "ArrowLeft" && open) {
            event.preventDefault();
            event.stopPropagation();
            close();
          }
        }}
        onClick={() => { if (!disabled) openPanel(true); }}>
        <span className="gl-menu-item-icon">{icon && <Icon name={icon} size="sm" />}</span>
        <span className="gl-menu-item-body"><span className="gl-menu-item-label">{label}</span></span>
        <Icon name="chevron-right" size="sm" />
      </button>
      {open && createPortal(
        <MenuContext.Provider value={parent}>
          <div ref={panelRef} id={id} role="menu" aria-label={label}
            className="gl-menu gl-menu--submenu" tabIndex={-1}
            data-nested={parent?.nested ? "" : undefined}
            onMouseEnter={cancelIntent} onMouseLeave={scheduleClose}
            style={{ position: "fixed", left: placement.x, top: placement.y }}>
            {regular}
            {destructive.length > 0 && (
              <>
                {regular.length > 0 && <MenuSeparator />}
                <div className="gl-menu-destructive-group" role="group" aria-label="Destructive actions">
                  {destructive}
                </div>
              </>
            )}
          </div>
        </MenuContext.Provider>,
        document.body,
      )}
    </div>
  );
}

export type MenuButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "title"> & {
  label: string;
  children: ReactNode;
  menuLabel?: string;
};

export function MenuButton({ label, children, menuLabel, className, disabled, onClick, ...rest }: MenuButtonProps) {
  const id = useId();
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const [position, setPosition] = useState<MenuPosition | null>(null);
  const showMenu = () => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (rect) setPosition({ x: rect.left, y: rect.bottom + 6 });
  };
  return (
    <>
      <button {...rest} ref={triggerRef} type="button" disabled={disabled}
        className={["gl-menu-button", className].filter(Boolean).join(" ")}
        aria-haspopup="menu" aria-expanded={position !== null}
        aria-controls={position ? id : undefined}
        onClick={(event) => {
          onClick?.(event);
          if (!event.defaultPrevented) {
            if (position) setPosition(null);
            else showMenu();
          }
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" && !position) {
            event.preventDefault();
            showMenu();
          }
        }}>
        {label}<Icon name="chevron-down" size="sm" />
      </button>
      {position && <Menu id={id} position={position} onClose={() => setPosition(null)}
        restoreFocusRef={triggerRef} ignoreRef={triggerRef} ariaLabel={menuLabel ?? label}>{children}</Menu>}
    </>
  );
}

export interface ContextMenuProps {
  children: ReactNode;
  /** Omit position to attach right-click/keyboard handling to a trigger. */
  trigger?: ReactNode;
  position?: MenuPosition;
  onClose?: () => void;
  className?: string;
  ariaLabel?: string;
  ignoreRef?: RefObject<HTMLElement | null>;
}

type ContextTriggerEvents = {
  onContextMenu?: (event: ReactMouseEvent<HTMLElement>) => void;
  onKeyDown?: (event: ReactKeyboardEvent<HTMLElement>) => void;
};

export function ContextMenu({
  children, trigger, position, onClose, className, ariaLabel, ignoreRef,
}: ContextMenuProps) {
  const [uncontrolledPosition, setUncontrolledPosition] = useState<MenuPosition | null>(null);
  const location = position ?? uncontrolledPosition;
  const close = () => {
    if (position === undefined) setUncontrolledPosition(null);
    onClose?.();
  };
  const openAtPointer = (event: ReactMouseEvent<HTMLElement>) => {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.focus();
    setUncontrolledPosition({ x: event.clientX, y: event.clientY });
  };
  const openAtKeyboard = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (!isMenuKey(event)) return;
    event.preventDefault();
    event.stopPropagation();
    const active = document.activeElement;
    const rect = (active instanceof HTMLElement ? active : event.currentTarget).getBoundingClientRect();
    setUncontrolledPosition({ x: rect.left + 16, y: rect.top + 16 });
  };
  if (trigger === undefined) {
    return location ? <Menu position={location} onClose={close}
      className={className} ariaLabel={ariaLabel} ignoreRef={ignoreRef}>{children}</Menu> : null;
  }
  // Decorate the actual trigger rather than wrapping a button in an interactive
  // span: this preserves its accessible role, focus and native click behavior.
  const triggerElement = isValidElement<ContextTriggerEvents>(trigger)
    ? cloneElement(trigger, {
      onContextMenu: (event: ReactMouseEvent<HTMLElement>) => {
        trigger.props.onContextMenu?.(event);
        if (!event.defaultPrevented) openAtPointer(event);
      },
      onKeyDown: (event: ReactKeyboardEvent<HTMLElement>) => {
        trigger.props.onKeyDown?.(event);
        if (!event.defaultPrevented) openAtKeyboard(event);
      },
    })
    : <button type="button" className="gl-menu-button"
        onContextMenu={openAtPointer} onKeyDown={openAtKeyboard}>{trigger}</button>;
  return (
    <>
      {triggerElement}
      {location && <Menu position={location} onClose={close}
        className={className} ariaLabel={ariaLabel} ignoreRef={ignoreRef}>{children}</Menu>}
    </>
  );
}
