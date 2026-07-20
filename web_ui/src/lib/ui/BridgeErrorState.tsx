import type { BridgeRejection } from "../bridge-core/islandState";

/**
 * The visible replacement for a rejected bridge payload - the shared shape
 * every island renders INSTEAD of its normal controls when parseIslandState()
 * rejects a payload, not as a dismissible banner alongside them. Once a
 * payload has been rejected, whatever was on screen is stale by definition,
 * and leaving a working-looking surface up next to a warning invites the
 * user to interact with state the desktop side no longer agrees with.
 * Replacing the surface makes the failure honest.
 *
 * Extracted here (lib/ui/'s first real component - see the master plan's
 * Phase 1 lib/ui/ checklist entry) once 4 islands had independently grown
 * byte-for-byte identical copies of this exact shape (composer, token-
 * counter, notification, command-palette) - real, verified duplication, not
 * a speculative "shared components might be useful" build-ahead-of-need.
 * The other lib/ui/ candidates named in section 3.1 (button, listbox,
 * popover, toast, markdown renderer, code editor wrapper, IME-safe inputs)
 * remain deferred: none of them yet have two independent real
 * implementations to generalize a correct shape from.
 *
 * className is intentionally still owned by the calling island, not baked
 * in here - each island's own background/border/width for its error surface
 * legitimately differs (token-counter is a fixed-width HUD panel; the
 * others aren't), and forcing that to be identical across islands would be
 * inventing a constraint that doesn't actually exist, not removing
 * duplication. Only the fields that were genuinely identical everywhere -
 * title/reason/details layout, and the version-mismatch hint (composer had
 * this, the other three simply never received it - the text itself is
 * fully generic, not composer-specific, so this also fixes a real gap in
 * the other three, not just deduplicating composer's copy) - are shared.
 */
export interface BridgeErrorStateProps {
  /** e.g. "Composer unavailable", "Notifications unavailable". */
  title: string;
  rejection: BridgeRejection;
  /** The calling island's own wrapper classes (shell + error modifier). */
  className: string;
}

export function BridgeErrorState({ title, rejection, className }: BridgeErrorStateProps) {
  return (
    <div className={className} role="alert" aria-live="assertive">
      <div className="bridge-error-title">{title}</div>
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
    </div>
  );
}
