import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
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

export type ToastProps = Omit<ToastItem, "id"> & {
  className?: string;
  /** Backend notifications must dispatch their dismiss intent on click. */
  dismissImmediately?: boolean;
};

export function Toast({
  title, variant, details, action, onDismiss, className, dismissImmediately = false,
}: ToastProps) {
  const [closing, setClosing] = useState(false);
  const closingRef = useRef(false);
  const exitTimer = useRef<number | null>(null);
  const timer = useRef<number | null>(null);
  const startedAt = useRef(0);
  const remaining = useRef(TRANSIENT_NOTIFICATION_MS - (dismissImmediately ? 0 : EXIT_MS));
  const pointerInside = useRef(false);
  const focusInside = useRef(false);

  const close = useCallback(() => {
    if (closingRef.current) return;
    closingRef.current = true;
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    setClosing(true);
    if (dismissImmediately || window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) {
      onDismiss?.();
    } else {
      exitTimer.current = window.setTimeout(() => onDismiss?.(), EXIT_MS);
    }
  }, [dismissImmediately, onDismiss]);

  const pause = useCallback(() => {
    if (timer.current === null) return;
    window.clearTimeout(timer.current);
    timer.current = null;
    remaining.current = Math.max(0, remaining.current - (Date.now() - startedAt.current));
  }, []);

  const resume = useCallback(() => {
    if ((variant !== "info" && variant !== "success") || pointerInside.current ||
        focusInside.current || closingRef.current || timer.current !== null) return;
    startedAt.current = Date.now();
    timer.current = window.setTimeout(() => {
      timer.current = null;
      close();
    }, Math.max(0, remaining.current));
  }, [variant, close]);

  useEffect(() => () => {
    if (exitTimer.current !== null) window.clearTimeout(exitTimer.current);
  }, []);

  // Focus and pointer events pause the *same* clock immediately. In particular,
  // an imperative .focus() must clear it before Vitest advances fake timers.
  useEffect(() => {
    resume();
    return pause;
  }, [resume, pause]);

  return (
    <div role={variant === "error" ? "alert" : "status"}
      className={["gl-toast", "gl-toast--" + variant, className].filter(Boolean).join(" ")}
      data-state={closing ? "closing" : "open"} aria-hidden={closing || undefined}
      onMouseEnter={() => { pointerInside.current = true; pause(); }}
      onMouseLeave={() => { pointerInside.current = false; resume(); }}
      onFocusCapture={() => { focusInside.current = true; pause(); }}
      onBlurCapture={(event) => {
        if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) {
          focusInside.current = false;
          resume();
        }
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
  const [expandedSignature, setExpandedSignature] = useState<string | null>(null);
  const all: ToastItem[] = [
    ...(backendNotification ? [{
      id: backendNotification.id, title: backendNotification.message,
      variant: backendNotification.msgType, onDismiss: backendNotification.onDismiss,
    }] : []),
    ...client,
  ];
  const hidden = Math.max(0, all.length - 3);
  const signature = all.map((item) => item.id).join("|");
  // A new notification collapses the stack again even after an earlier expansion.
  const expanded = hidden > 0 && expandedSignature === signature;
  const visible = expanded ? all : all.slice(0, 3);

  return createPortal(
    <div className="gl-toaster" role="region" aria-label="Notifications">
      {visible.map((item) => (
        <Toast key={item.id} title={item.title} variant={item.variant}
          details={item.details} action={item.action}
          dismissImmediately={backendNotification?.id === item.id}
          onDismiss={item.onDismiss ?? (() => toast.dismiss(item.id))} />
      ))}
      {hidden > 0 && (
        <button type="button" className="gl-toaster-more" aria-expanded={expanded}
          onClick={() => setExpandedSignature(expanded ? null : signature)}>
          {expanded ? "Show fewer" : "+" + formatCount(hidden) + " more"}
        </button>
      )}
    </div>,
    document.body,
  );
}
