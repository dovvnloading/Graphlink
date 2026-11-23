import {
  Children, cloneElement, useCallback, useEffect, useId, useLayoutEffect, useRef, useState,
  type FocusEvent, type MouseEvent, type ReactElement,
} from "react";
import { createPortal } from "react-dom";
import { Kbd } from "./Kbd";
import "./tooltip.css";

type TriggerEvents = {
  onMouseEnter?: (event: MouseEvent<HTMLElement>) => void;
  onMouseLeave?: (event: MouseEvent<HTMLElement>) => void;
  onFocus?: (event: FocusEvent<HTMLElement>) => void;
  onBlur?: (event: FocusEvent<HTMLElement>) => void;
  "aria-describedby"?: string;
};

export type TooltipProps = {
  label: string;
  shortcut?: string;
  children: ReactElement<TriggerEvents>;
  placement?: "bottom" | "top";
};

const HOVER_DELAY_MS = 500;
const VIEWPORT_MARGIN = 8;
const TRIGGER_GAP = 8;

export function Tooltip({ label, shortcut, children, placement = "bottom" }: TooltipProps) {
  const child = Children.only(children);
  const id = useId();
  const [visible, setVisible] = useState(false);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const timeout = useRef<number | null>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const tip = useRef<HTMLDivElement | null>(null);
  const pointerFocus = useRef(false);

  const cancelDelay = useCallback(() => {
    if (timeout.current !== null) window.clearTimeout(timeout.current);
    timeout.current = null;
  }, []);
  const hide = useCallback(() => {
    cancelDelay();
    setVisible(false);
  }, [cancelDelay]);

  useEffect(() => {
    const onPointerDown = () => { pointerFocus.current = true; hide(); };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && (visible || timeout.current !== null)) {
        event.preventDefault();
        event.stopPropagation();
        hide();
      } else {
        pointerFocus.current = false;
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [visible, hide]);

  useEffect(() => () => {
    if (timeout.current !== null) window.clearTimeout(timeout.current);
  }, []);

  useLayoutEffect(() => {
    if (!visible) return;
    const calculate = () => {
      if (!trigger.current || !tip.current) return;
      const anchor = trigger.current.getBoundingClientRect();
      const tooltip = tip.current.getBoundingClientRect();
      const below = anchor.bottom + TRIGGER_GAP;
      const above = anchor.top - tooltip.height - TRIGGER_GAP;
      const fitsBelow = below + tooltip.height <= window.innerHeight - VIEWPORT_MARGIN;
      const fitsAbove = above >= VIEWPORT_MARGIN;
      // Flip before clamping: a tooltip must not cover its trigger.
      const top = placement === "top"
        ? (fitsAbove ? above : below)
        : (fitsBelow || !fitsAbove ? below : above);
      const left = Math.max(VIEWPORT_MARGIN, Math.min(
        anchor.left + anchor.width / 2 - tooltip.width / 2,
        window.innerWidth - tooltip.width - VIEWPORT_MARGIN,
      ));
      setPosition((old) => old.top === top && old.left === left ? old : { top, left });
    };
    calculate();
    window.addEventListener("resize", calculate);
    window.addEventListener("scroll", calculate, true);
    return () => {
      window.removeEventListener("resize", calculate);
      window.removeEventListener("scroll", calculate, true);
    };
  }, [visible, placement]);

  const describedBy = visible ? [child.props["aria-describedby"], id].filter(Boolean).join(" ") : child.props["aria-describedby"];

  return (
    <>
      {cloneElement(child, {
        "aria-describedby": describedBy,
        onMouseEnter: (event: MouseEvent<HTMLElement>) => {
          child.props.onMouseEnter?.(event);
          trigger.current = event.currentTarget;
          cancelDelay();
          timeout.current = window.setTimeout(() => {
            timeout.current = null;
            setVisible(true);
          }, HOVER_DELAY_MS);
        },
        onMouseLeave: (event: MouseEvent<HTMLElement>) => {
          child.props.onMouseLeave?.(event);
          hide();
        },
        onFocus: (event: FocusEvent<HTMLElement>) => {
          child.props.onFocus?.(event);
          trigger.current = event.currentTarget;
          if (!pointerFocus.current) {
            cancelDelay();
            setVisible(true);
          }
        },
        onBlur: (event: FocusEvent<HTMLElement>) => {
          child.props.onBlur?.(event);
          hide();
        },
      })}
      {visible && createPortal(
        <div ref={tip} id={id} role="tooltip" className="gl-tooltip" style={position}>
          <span>{label}</span>
          {shortcut && <Kbd shortcut={shortcut} />}
        </div>,
        document.body,
      )}
    </>
  );
}
