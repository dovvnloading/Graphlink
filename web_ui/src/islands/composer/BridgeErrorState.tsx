import type { BridgeRejection } from "./bridge";

/**
 * The visible replacement for the old silent freeze.
 *
 * Rendered INSTEAD of the composer controls, not as a dismissible banner
 * alongside them, and that is deliberate: once a payload has been rejected,
 * whatever is on screen is stale by definition, and leaving a working-looking
 * input box above a warning invites the user to type into a composer whose
 * state the desktop side no longer agrees with. Replacing the surface makes
 * the failure honest.
 *
 * Colors come from the island's own --gl-composer-* tokens rather than new
 * literals, so this participates in theming like everything else and stays
 * inside the raw-hex ban.
 */
export function BridgeErrorState({ rejection }: { rejection: BridgeRejection }) {
  return (
    <main className="composer-shell bridge-error" role="alert" aria-live="assertive">
      <div className="bridge-error-title">Composer unavailable</div>
      <p className="bridge-error-reason">{rejection.reason}</p>
      {rejection.details.length > 0 && (
        <ul className="bridge-error-details">
          {rejection.details.map((detail) => (
            <li key={detail}>{detail}</li>
          ))}
        </ul>
      )}
      <p className="bridge-error-hint">
        {rejection.kind === "version"
          ? "Rebuilding the app's interface assets usually resolves this."
          : "This is a bug - the desktop app and this interface disagree about the message format."}
      </p>
    </main>
  );
}
