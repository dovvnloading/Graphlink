import type { BridgeRejection } from "./bridge";

/**
 * See composer/BridgeErrorState.tsx for the full rationale (same pattern:
 * replaces the display entirely on a rejected payload, rather than leaving a
 * stale-looking counter on screen next to a warning).
 */
export function TokenCounterErrorState({ rejection }: { rejection: BridgeRejection }) {
  return (
    <div className="token-counter-shell bridge-error" role="alert" aria-live="assertive">
      <div className="bridge-error-title">Token counter unavailable</div>
      <p className="bridge-error-reason">{rejection.reason}</p>
      {rejection.details.length > 0 && (
        <ul className="bridge-error-details">
          {rejection.details.map((detail) => (
            <li key={detail}>{detail}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
