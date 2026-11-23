import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { Button } from "./Button";
import { Disclosure } from "./Disclosure";
import { Icon } from "./icons/Icon";
import { IconButton } from "./IconButton";
import { formatCount } from "./format";
import "./toast.css";

export const TRANSIENT_NOTIFICATION_MS = 5000;
const EXIT_MS = 120;
export type ToastVariant = "info" | "success" | "warning" | "error";

export type ToastAction = { label: string; onClick: () => void };
export type ToastOptions = {
  title?: string;
  /** An alias for title when adapting existing notifications. */
  message?: string;
  variant?: ToastVariant;
  /** An alias for variant for consumers of the client toast API. */
  type?: ToastVariant;
  details?: string;
  action?: ToastAction;
  onDismiss?: () => void;
};

export type ToastItem = {
  id: string;
  title: string;
  variant: ToastVariant;
  details?: string;
  action?: ToastAction;
  onDismiss?: () => void;
};

let nextId = 1;
let clientToasts: ToastItem[] = [];
const listeners = new Set<() => void>();
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
function getSnapshot() { return clientToasts; }
function notify() { for (const listener of listeners) listener(); }

/** A small external store, usable outside React (including intent-error handlers). */
export const toast = {
  show(options: ToastOptions): string {
    const id = "client-toast-" + nextId++;
    clientToasts = [{
      id, title: options.title ?? options.message ?? "", variant: options.variant ?? options.type ?? "info",
      details: options.details, action: options.action, onDismiss: options.onDismiss,
    }, ...clientToasts];
    notify();
    return id;
  },
  dismiss(id: string): void {
    const item = clientToasts.find((entry) => entry.id === id);
    if (!item) return;
    clientToasts = clientToasts.filter((entry) => entry.id !== id);
    notify();
    item.onDismiss?.();
  },
  /** Removes client toasts; backend-owned notifications still use their dismiss intent. */
  clear(): void {
    if (!clientToasts.length) return;
    clientToasts = [];
    notify();
  },
};

export type ToastProps = Omit<ToastItem, "id"> & { className?: string };

export function Toast({ title, variant, details, action, onDismiss, className }: ToastProps) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [closing, setClosing] = useState(false);
  const closingRef = useRef(false);
  const exitTimer = useRef<number | null>(null);
  const remaining = useRef(TRANSIENT_NOTIFICATION_MS - EXIT_MS);

  function close() {
    if (closingRef.current) return;
    closingRef.current = true;
    setClosing(true);
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) {
      onDismiss?.();
    } else {
      exitTimer.current = window.setTimeout(() => onDismiss?.(), EXIT_MS);
    }
  }

  useEffect(() => () => {
    if (exitTimer.current !== null) window.clearTimeout(exitTimer.current);
  }, []);

  // Each visible transient toast owns its clock. Pause while the pointer is
  // inside or keyboard focus is within any of its controls.
  useEffect(() => {
    if (variant !== "info" && variant !== "success") return;
    if (hovered || focused || closingRef.current) return;
    const started = Date.now();
    const timer = window.setTimeout(close, Math.max(0, remaining.current));
    return () => {
      window.clearTimeout(timer);
      remaining.current = Math.max(0, remaining.current - (Date.now() - started));
    };
    // close is recreated on render, but only changes to these state values
    // should change the clock. onDismiss is stable for backend notifications.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [variant, hovered, focused, onDismiss]);

  return (
    <div role={variant === "error" ? "alert" : "status"}
      className={["gl-toast", "gl-toast--" + variant, className].filter(Boolean).join(" ")}
      data-state={closing ? "closing" : "open"}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocusCapture={() => setFocused(true)}
      onBlurCapture={(event) => {
        if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setFocused(false);
      }}>
      <Icon name={"status-" + variant as "status-info" | "status-success" | "status-warning" | "status-error"} size="lg" />
      <div className="gl-toast-main">
        <strong className="gl-toast-title">{title}</strong>
        {details && <Disclosure title="Details">
          <div className="gl-toast-details">
            <pre>{details}</pre>
            <IconButton icon="copy" label="Copy details" size="sm"
              onClick={() => { void navigator.clipboard?.writeText(details); }} />
          </div>
        </Disclosure>}
        {action && <Button size="sm" onClick={action.onClick}>{action.label}</Button>}
      </div>
      <IconButton icon="close" label="Dismiss notification" size="sm"
        className="notification-dismiss" onClick={close} />
    </div>
  );
}

export type BackendToast = {
  id: string;
  message: string;
  msgType: ToastVariant;
  onDismiss: () => void;
};

export function Toaster({ backendNotification }: { backendNotification?: BackendToast | null }) {
  const client = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const [expanded, setExpanded] = useState(false);
  const all: ToastItem[] = [
    ...(backendNotification ? [{
      id: backendNotification.id, title: backendNotification.message,
      variant: backendNotification.msgType, onDismiss: backendNotification.onDismiss,
    }] : []),
    ...client,
  ];
  const hidden = Math.max(0, all.length - 3);
  const visible = expanded ? all : all.slice(0, 3);

  return createPortal(
    <div className="gl-toaster" aria-label="Notifications">
      {visible.map((item) => (
        <Toast key={item.id} title={item.title} variant={item.variant}
          details={item.details} action={item.action}
          onDismiss={item.onDismiss ?? (() => toast.dismiss(item.id))} />
      ))}
      {hidden > 0 && (
        <button type="button" className="gl-toaster-more" aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}>
          {expanded ? "Show fewer" : "+" + formatCount(hidden) + " more"}
        </button>
      )}
    </div>,
    document.body,
  );
}
