import { useEffect, useSyncExternalStore } from "react";
import type { ComposerStore } from "./composerStore";

/** Info and success banners are confirmations; they clear themselves after
 * this long. Warnings and errors need attention and stay until dismissed.
 * Dismissing goes through the backend intent, so a reload does not bring a
 * stale confirmation back. */
export const TRANSIENT_NOTIFICATION_MS = 5000;

export function NotificationBanner({ store }: { store: ComposerStore }) {
  const notification = useSyncExternalStore(store.subscribe, store.getNotification);
  const transient = notification.visible
    && (notification.msgType === "info" || notification.msgType === "success");

  useEffect(() => {
    if (!transient) return;
    const timer = window.setTimeout(() => store.dismissNotification(), TRANSIENT_NOTIFICATION_MS);
    return () => window.clearTimeout(timer);
  }, [transient, notification.message, notification.msgType, store]);

  if (!notification.visible) return null;
  return (
    <div className={`notification-banner notification-${notification.msgType}`} role="status">
      <span className="notification-message">{notification.message}</span>
      <button
        type="button"
        className="notification-dismiss"
        aria-label="Dismiss notification"
        onClick={() => store.dismissNotification()}
      >
        ×
      </button>
    </div>
  );
}
