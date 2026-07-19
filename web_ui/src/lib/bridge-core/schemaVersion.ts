/**
 * Minimum-compatible schema-version negotiation, shared by every island.
 *
 * Replaces the old `state?.schemaVersion !== 1` exact-match gate, whose failure
 * mode was to return null and never call the state listener - leaving the UI
 * silently frozen on whatever it last rendered (or on the static browser-preview
 * mock forever), with nothing on screen indicating anything was wrong.
 *
 * Why version skew is a real scenario and not a hypothetical: the built island
 * assets under assets/ are committed, and the Python bootstrap only rebuilds
 * them when it detects staleness. A stale or partial build genuinely can leave
 * the JS reader older than the Python sender.
 *
 * The negotiation is deliberately two-sided:
 *
 *   1. `payload.schemaVersion >= READER_MIN_COMPATIBLE_SCHEMA_VERSION`
 *      The reader refuses payloads older than it can handle - the fields it
 *      needs genuinely may not be there.
 *
 *   2. `READER_SCHEMA_VERSION >= payload.minCompatibleSchemaVersion`
 *      The sender gets to declare that readers below some version can no
 *      longer handle it. This is what makes a BREAKING change explicit rather
 *      than something the reader has to infer from a version number.
 *
 * A payload NEWER than the reader is accepted whenever the sender says it is
 * still compatible: unknown extra fields are tolerated by the generated
 * validator, which is exactly what "additive fields allowed" means.
 */

/** The payload version this build of the island reads. */
export const READER_SCHEMA_VERSION = 1;

/** The oldest payload version this build can still read correctly. */
export const READER_MIN_COMPATIBLE_SCHEMA_VERSION = 1;

export type VersionVerdict =
  | { compatible: true }
  | { compatible: false; reason: string };

interface VersionedEnvelope {
  schemaVersion?: unknown;
  minCompatibleSchemaVersion?: unknown;
}

export function checkSchemaCompatibility(payload: unknown): VersionVerdict {
  if (typeof payload !== "object" || payload === null) {
    return { compatible: false, reason: "The update from the desktop app was not an object." };
  }

  const envelope = payload as VersionedEnvelope;
  const version = envelope.schemaVersion;

  if (typeof version !== "number" || !Number.isFinite(version)) {
    return {
      compatible: false,
      reason: "The update from the desktop app did not declare a schema version.",
    };
  }

  if (version < READER_MIN_COMPATIBLE_SCHEMA_VERSION) {
    return {
      compatible: false,
      reason:
        `The desktop app sent schema version ${version}, but this interface needs at ` +
        `least ${READER_MIN_COMPATIBLE_SCHEMA_VERSION}.`,
    };
  }

  // Absent means "no explicit floor declared" - treat as compatible rather than
  // rejecting, so a sender predating this field is not spuriously refused.
  const senderFloor = envelope.minCompatibleSchemaVersion;
  if (typeof senderFloor === "number" && Number.isFinite(senderFloor)) {
    if (READER_SCHEMA_VERSION < senderFloor) {
      return {
        compatible: false,
        reason:
          `The desktop app requires an interface of at least schema version ` +
          `${senderFloor}, but this one is version ${READER_SCHEMA_VERSION}. ` +
          `The bundled interface is out of date.`,
      };
    }
  }

  return { compatible: true };
}
