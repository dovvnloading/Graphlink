import {
  Fragment, useEffect, useId, useLayoutEffect, useRef, useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { createPortal } from "react-dom";
import { OverlayProviderIfMissing, useOverlays } from "../overlays/overlays";
import { Icon } from "./icons/Icon";
import type { IconName } from "./icons/registry";
import "./select.css";

export interface SelectOption {
  value: string;
  label: string;
  description?: string;
  icon?: IconName;
  group?: string;
  disabled?: boolean;
}

export interface SelectProps {
  value: string;
  options: readonly SelectOption[];
  onChange: (value: string) => void;
  label?: string;
  ariaLabel?: string;
  labelledBy?: string;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
}

const ANCHOR_MARGIN = 8;
const ANCHOR_GAP = 6;
const FOCUSABLE =
  'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** This wrapper also permits the kit to be used in isolated renderers/tests. */
export function Select(props: SelectProps) {
  return (
    <OverlayProviderIfMissing>
      <SelectControl {...props} />
    </OverlayProviderIfMissing>
  );
}

function SelectControl({
  value, options, onChange, label, ariaLabel, labelledBy,
  disabled = false, placeholder = "Select…", className,
}: SelectProps) {
  const overlays = useOverlays();
  const id = useId();
  const name = `select-${id}`;
  const triggerId = `${id}-trigger`;
  const labelId = `${id}-label`;
  const listId = `${id}-listbox`;
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const listboxRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const [placement, setPlacement] = useState<{
    top: number; left: number; minWidth: number; maxHeight: number;
  } | null>(null);
  const [query, setQuery] = useState("");
  const [activeValue, setActiveValue] = useState(value);
  const typeAhead = useRef({ text: "", time: 0 });
  const isOpen = overlays.isOpen(name);
  const closing = overlays.isClosing(name);
  const selected = options.find((option) => option.value === value);
  const searchable = options.length > 8;
  const visible = options.filter((option) =>
    !query || (option.label + " " + (option.description ?? "")).toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  const enabled = visible.filter((option) => !option.disabled);
  const active = enabled.find((option) => option.value === activeValue) ?? enabled[0];
  const activeId = isOpen && active ? `${id}-option-${options.indexOf(active)}` : undefined;

  // Keep the active descendant visible as arrows/type-ahead traverse long lists.
  useEffect(() => {
    if (isOpen && activeId) document.getElementById(activeId)?.scrollIntoView?.({ block: "nearest" });
  }, [isOpen, activeId]);

  // Anchored to the trigger while open (#1146): re-placed on window resize,
  // any ancestor scroll, and trigger/panel size changes (options arriving,
  // query filtering). Listeners exist only while the panel is open.
  useLayoutEffect(() => {
    if (!isOpen) return;
    const trigger = triggerRef.current;
    const panel = panelRef.current;
    if (!trigger || !panel) return;
    const place = () => {
      const triggerRect = trigger.getBoundingClientRect();
      const panelRect = panel.getBoundingClientRect();
      const below = window.innerHeight - triggerRect.bottom - ANCHOR_GAP - ANCHOR_MARGIN;
      const above = triggerRect.top - ANCHOR_GAP - ANCHOR_MARGIN;
      // Open on the side with room; cap the height to that side's space so
      // every option stays reachable by scrolling inside the panel.
      const openAbove = panelRect.height > below && above > below;
      const maxHeight = Math.max(0, Math.floor(openAbove ? above : below));
      const height = Math.min(panelRect.height, maxHeight);
      const top = openAbove
        ? Math.max(ANCHOR_MARGIN, triggerRect.top - height - ANCHOR_GAP)
        : triggerRect.bottom + ANCHOR_GAP;
      const left = Math.min(
        Math.max(ANCHOR_MARGIN, triggerRect.left),
        Math.max(ANCHOR_MARGIN, window.innerWidth - panelRect.width - ANCHOR_MARGIN),
      );
      setPlacement((previous) => previous?.top === top && previous.left === left
        && previous.minWidth === triggerRect.width && previous.maxHeight === maxHeight
        ? previous : { top, left, minWidth: triggerRect.width, maxHeight });
    };
    place();
    window.addEventListener("resize", place);
    // Capture: scrolls of any ancestor container, not just the window.
    document.addEventListener("scroll", place, { capture: true, passive: true });
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(place);
    resize?.observe(trigger);
    resize?.observe(panel);
    return () => {
      window.removeEventListener("resize", place);
      document.removeEventListener("scroll", place, { capture: true });
      resize?.disconnect();
    };
  }, [isOpen, query, options.length]);

  useEffect(() => {
    if (!isOpen) return;
    overlays.registerSurfaceElement(name, panelRef.current);
    return () => overlays.registerSurfaceElement(name, null);
  }, [isOpen, name, overlays.registerSurfaceElement]);

  useEffect(() => {
    if (!isOpen) return;
    if (searchable) searchRef.current?.focus();
    else listboxRef.current?.focus();
  }, [isOpen, searchable]);

  const openList = (initial?: string) => {
    if (disabled) return;
    setQuery("");
    setActiveValue(initial ?? (selected && !selected.disabled ? selected.value : options.find((option) => !option.disabled)?.value ?? ""));
    typeAhead.current = { text: "", time: 0 };
    overlays.openNested(name, "popover", triggerRef.current);
  };

  const closeList = () => {
    if (isOpen) overlays.closeLayer(name);
  };

  // Do not leave a phantom nested layer if a parent conditionally removes this field.
  useEffect(() => () => overlays.closeLayer(name), [name, overlays.closeLayer]);

  const choose = (option: SelectOption) => {
    if (option.disabled) return;
    onChange(option.value);
    closeList();
  };

  const move = (direction: number) => {
    if (!enabled.length) return;
    const index = enabled.findIndex((option) => option.value === active?.value);
    setActiveValue(enabled[(index + direction + enabled.length) % enabled.length].value);
  };

  const matchTypeAhead = (letter: string, now: number) => {
    const prefix = (now - typeAhead.current.time > 700 ? "" : typeAhead.current.text) + letter.toLocaleLowerCase();
    const candidates = options.filter((option) => !option.disabled);
    let found = candidates.find((option) => option.label.toLocaleLowerCase().startsWith(prefix));
    if (!found) found = candidates.find((option) => option.label.toLocaleLowerCase().startsWith(letter.toLocaleLowerCase()));
    typeAhead.current = { text: found && found.label.toLocaleLowerCase().startsWith(prefix) ? prefix : letter.toLocaleLowerCase(), time: now };
    if (found) setActiveValue(found.value);
    return found;
  };

  const movePast = (reverse: boolean) => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const scope = trigger.closest<HTMLElement>('[role="dialog"][aria-modal="true"]') ?? document.body;
    const controls = [...scope.querySelectorAll<HTMLElement>(FOCUSABLE)]
      .filter((element) => element.tabIndex >= 0 && !element.closest("[hidden], [inert], [aria-hidden=true]")
        && !panelRef.current?.contains(element));
    const index = controls.indexOf(trigger);
    const next = controls[(index + (reverse ? -1 : 1) + controls.length) % controls.length];
    closeList();
    (next ?? trigger).focus();
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    const inSearch = event.target === searchRef.current;
    // An IME's confirmation key commits composed text, not an option (#1145).
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Escape" && isOpen) {
      event.preventDefault();
      event.stopPropagation();
      closeList();
      return;
    }
    if (event.key === "Tab" && isOpen) {
      event.preventDefault();
      movePast(event.shiftKey);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!isOpen) {
        const candidates = options.filter((option) => !option.disabled);
        const index = candidates.findIndex((option) => option.value === value);
        const next = candidates[index < 0 ? 0 : (index + (event.key === "ArrowDown" ? 1 : -1) + candidates.length) % candidates.length];
        openList(next?.value);
      } else move(event.key === "ArrowDown" ? 1 : -1);
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      // Editing search text: Home/End (and their Shift/Ctrl variants) move
      // the caret natively. Non-searchable listboxes keep option navigation.
      if (inSearch) return;
      event.preventDefault();
      const candidates = isOpen ? enabled : options.filter((option) => !option.disabled);
      const next = event.key === "Home" ? candidates[0] : candidates[candidates.length - 1];
      if (!isOpen) openList(next?.value);
      else if (next) setActiveValue(next.value);
      return;
    }
    if (event.key === "Enter" || (event.key === " " && !inSearch)) {
      event.preventDefault();
      if (!isOpen) openList();
      else if (active) choose(active);
      return;
    }
    if (!event.altKey && !event.ctrlKey && !event.metaKey && event.key.length === 1 && !inSearch) {
      const now = event.timeStamp;
      const found = matchTypeAhead(event.key, now);
      if (!isOpen && found) {
        openList(found.value);
        typeAhead.current = { text: event.key.toLocaleLowerCase(), time: now };
      }
    }
  };

  return (
    <div className={["gl-select", className].filter(Boolean).join(" ")}>
      {label && <span className="gl-select-label" id={labelId}>{label}</span>}
      <button
        id={triggerId} ref={triggerRef} type="button" className="gl-select-trigger"
        data-overlay-trigger={name}
        aria-haspopup="listbox" aria-expanded={isOpen}
        aria-controls={isOpen ? listId : undefined}
        aria-label={label || labelledBy ? undefined : ariaLabel}
        aria-labelledby={label ? labelId : labelledBy}
        disabled={disabled}
        onClick={() => isOpen ? closeList() : openList()}
        onKeyDown={onKeyDown}
      >
        <span className="gl-select-value">{selected?.label ?? placeholder}</span>
        <Icon name="chevron-down" size="sm" className="gl-select-chevron" />
      </button>
      {(isOpen || closing) && createPortal(
        <div
          ref={panelRef}
          className="gl-select-panel"
          data-state={closing ? "closing" : "open"}
          data-nested={overlays.isNested(name) ? "" : undefined}
          aria-hidden={closing ? true : undefined}
          style={placement ? {
            top: placement.top, left: placement.left, minWidth: placement.minWidth,
            ["--gl-select-available-height" as string]: `${placement.maxHeight}px`,
          } : undefined}
          onKeyDown={onKeyDown}
        >
          {isOpen && <>
            {searchable && (
              <input
                ref={searchRef} className="gl-select-search" type="search"
                role="combobox" aria-label={`Search ${ariaLabel ?? label ?? "options"}`}
                aria-autocomplete="list" aria-expanded="true"
                aria-controls={listId} aria-activedescendant={activeId}
                value={query}
                onChange={(event) => {
                  const next = event.target.value;
                  setQuery(next);
                  const first = options.find((option) => !option.disabled &&
                    (option.label + " " + (option.description ?? "")).toLocaleLowerCase().includes(next.toLocaleLowerCase()));
                  if (first) setActiveValue(first.value);
                }}
                placeholder="Search options"
              />
            )}
            <div ref={listboxRef} id={listId} className="gl-select-listbox" role="listbox"
              tabIndex={-1}
              aria-activedescendant={!searchable ? activeId : undefined}
              aria-label={ariaLabel ?? label ?? "Options"} aria-labelledby={label ? labelId : labelledBy}>
              {visible.length ? visible.map((option, index) => (
                <Fragment key={option.value}>
                  {option.group && (index === 0 || visible[index - 1].group !== option.group) &&
                    <div className="gl-select-group" role="presentation">{option.group}</div>}
                  <button
                    id={`${id}-option-${options.indexOf(option)}`}
                    type="button" role="option"
                    aria-selected={option.value === value}
                    aria-disabled={option.disabled || undefined}
                    disabled={option.disabled}
                    tabIndex={-1}
                    className={`gl-select-option${active?.value === option.value ? " is-active" : ""}${option.value === value ? " is-selected" : ""}`}
                    // A newly opened/repositioned panel can fire mouseenter under
                    // a stationary cursor. Only actual pointer movement should
                    // override the option the keyboard just activated.
                    onPointerMove={event => {
                      if (!option.disabled && event.pointerType !== "touch") setActiveValue(option.value);
                    }}
                    onClick={() => choose(option)}
                  >
                    {option.icon && <Icon name={option.icon} />}
                    <span className="gl-select-option-content">
                      <span className="gl-select-option-label">{option.label}</span>
                      {option.description && <span className="gl-select-option-description">{option.description}</span>}
                    </span>
                    {option.value === value && <Icon name="check" size="sm" />}
                  </button>
                </Fragment>
              )) : <div className="gl-select-empty">No matching options</div>}
            </div>
          </>}
        </div>,
        document.body,
      )}
    </div>
  );
}
