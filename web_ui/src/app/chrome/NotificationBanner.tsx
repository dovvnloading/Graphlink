import { useSyncExternalStore } from "react";
import type { ComposerStore } from "./composerStore";

export function NotificationBanner({ store }: { store: ComposerStore }) {
  const notification = useSyncExternalStore(store.subscribe, store.getNotification);
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
