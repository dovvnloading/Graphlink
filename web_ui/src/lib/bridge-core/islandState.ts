import { checkSchemaCompatibility } from "./schemaVersion";

/**
 * Why a payload was rejected, for a visible error state. `null` means the
 * last payload was fine.
 *
 * Generic across islands on purpose - originally lived inline in
 * composer/bridge.ts, which worked but meant every future island's own
 * bridge.ts would either duplicate this parse/reject shell or the project
 * would rediscover the need to extract it later. This is the shared half;
 * only the generated validator function passed into parseIslandState() below
 * is island-specific.
 */
export interface BridgeRejection {
  kind: "version" | "shape" | "parse";
  reason: string;
  details: string[];
}

export type RejectionListener = (rejection: BridgeRejection | null) => void;

export type ParseOutcome<T> =
  | { ok: true; state: T }
  | { ok: false; rejection: BridgeRejection };

/**
 * Matches the shape every island-codegen-generated `validate<Name>State()`
 * function has (see graphlink_app/graphlink_island_codegen.py's
 * _VALIDATOR_PREAMBLE). Declared structurally here rather than imported from
 * a generated file, so this module has no dependency on any specific
 * island's artifact - any future island's generated validator satisfies this
 * type without bridge-core needing to know it exists.
 */
export type StateValidator<T> = (
  value: unknown,
) => { ok: true; value: T } | { ok: false; errors: string[] };

/**
 * Parse and vet an incoming island payload: JSON-parse, check schema-version
 * compatibility, then run the island-specific generated validator. Every
 * rejection path returns a REASON, never a bare null - the failure mode this
 * replaces (composer/bridge.ts's original parseState()) collapsed "not valid
 * JSON", "wrong schema version", and "missing required fields" into a single
 * `null` the caller silently dropped, freezing the UI with no signal.
 */
export function parseIslandState<T>(
  payload: string,
  validate: StateValidator<T>,
): ParseOutcome<T> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(payload);
  } catch (error) {
    return {
      ok: false,
      rejection: {
        kind: "parse",
        reason: "The desktop app sent an update that could not be read as JSON.",
        details: [error instanceof Error ? error.message : String(error)],
      },
    };
  }

  const version = checkSchemaCompatibility(parsed);
  if (!version.compatible) {
    return {
      ok: false,
      rejection: { kind: "version", reason: version.reason, details: [] },
    };
  }

  const validated = validate(parsed);
  if (!validated.ok) {
    return {
      ok: false,
      rejection: {
        kind: "shape",
        reason: "The update from the desktop app did not match the expected format.",
        // Bounded: a badly wrong payload can produce a very long list, and an
        // error state built on this shows these to a human.
        details: validated.errors.slice(0, 8),
      },
    };
  }

  return { ok: true, state: validated.value };
}
