import type { BridgeRejection } from "./bridge";

/**
 * See composer/BridgeErrorState.tsx for the full rationale (same pattern:
 * replaces the display entirely on a rejected payload).
 */
export function NotificationErrorState({ rejection }: { rejection: BridgeRejection }) {
  return (
    <div className="notification-shell notification-error" role="alert" aria-live="assertive">
      <div className="notification-error-title">Notifications unavailable</div>
      <p className="notification-error-reason">{rejection.reason}</p>
      {rejection.details.length > 0 && (
        <ul className="notification-error-details">
          {rejection.details.map((detail) => (
            <li key={detail}>{detail}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
