import { useCallback, useSyncExternalStore } from "react";
import type { ComposerStore } from "./composerStore";
import { Toaster } from "../ui/Toast";

export { TRANSIENT_NOTIFICATION_MS } from "../ui/Toast";

/** Bridges the live backend notification topic into the kit toaster.
 * Dismissal remains store.dismissNotification(), which sends the original
 * fireIntent("notification", "dismiss", []) backend intent. */
export function NotificationToaster({ store }: { store: ComposerStore }) {
  const notification = useSyncExternalStore(store.subscribe, store.getNotification);
  const dismiss = useCallback(() => store.dismissNotification(), [store]);
  return <Toaster backendNotification={notification.visible ? {
    id: "backend-notification-" + notification.revision,
    message: notification.message,
    msgType: notification.msgType,
    onDismiss: dismiss,
  } : null} />;
}

/** Compatibility export for older notification call sites. */
export const NotificationBanner = NotificationToaster;
