import type { BridgeRejection } from "./bridge";

/**
 * See composer/BridgeErrorState.tsx for the full rationale (same pattern:
 * replaces the display entirely on a rejected payload).
 */
export function CommandPaletteErrorState({ rejection }: { rejection: BridgeRejection }) {
  return (
    <div className="palette-shell palette-error" role="alert" aria-live="assertive">
      <div className="palette-error-title">Command palette unavailable</div>
      <p className="palette-error-reason">{rejection.reason}</p>
      {rejection.details.length > 0 && (
        <ul className="palette-error-details">
          {rejection.details.map((detail) => (
            <li key={detail}>{detail}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
