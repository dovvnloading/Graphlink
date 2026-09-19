/**
 * The runtime half of the wire validators contracts/codegen.py generates.
 *
 * Each generated file (./generated/*.ts) holds its payload's shape as DATA -
 * one `[name, type, optional]` row per field - and builds its validator from
 * that table with `compileFields` once, at module load. The validators used to
 * be emitted as unrolled code, one block per field: ~190 minified bytes a
 * field, 87 KB across every topic, all of it in the initial chunk (every topic
 * is subscribed at startup), the largest single piece of app code there. A
 * table row costs ~25 bytes, and the checker compiled from it keeps the
 * unrolled code's shape at run time: one closure per field, no per-value type
 * dispatch.
 *
 * Behaviour is exactly the unrolled validators', message for message: a
 * missing or null required field is "missing required field", a present one
 * is type-checked, an absent or null optional field is skipped, and unknown
 * keys are tolerated on purpose - the JSON Schema marks the contract
 * additionalProperties:false because Python and the schema must not drift,
 * but an incoming payload carrying a field this build has never heard of is
 * the normal, expected shape of a NEWER compatible sender, and rejecting it
 * would defeat the additive-forward-compatibility the version negotiation
 * exists to provide.
 */

export type ValidationResult<T> =
  | { ok: true; value: T }
  | { ok: false; errors: string[] };

/** A field's type: "s" string, "b" boolean, "n" number; `e` a string
 * literal union, `a` an array of, `d` a string-keyed record of, `o` a nested
 * object with its own field table. */
export type WireType =
  | "s"
  | "b"
  | "n"
  | { readonly e: readonly string[] }
  | { readonly a: WireType }
  | { readonly d: WireType }
  | { readonly o: WireFields };

/** [name, type, optional (1) or required (0), wire default - sparse rows only] */
export type WireField = readonly [string, WireType, 0 | 1, unknown?];
export type WireFields = readonly WireField[];

type Check = (value: unknown, path: string, errors: string[]) => void;

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function compileType(type: WireType): Check {
  if (type === "s") {
    return (value, path, errors) => {
      if (typeof value !== "string") errors.push(path + ": expected string");
    };
  }
  if (type === "b") {
    return (value, path, errors) => {
      if (typeof value !== "boolean") errors.push(path + ": expected boolean");
    };
  }
  if (type === "n") {
    return (value, path, errors) => {
      if (typeof value !== "number") errors.push(path + ": expected number");
    };
  }
  if ("e" in type) {
    const allowed = type.e;
    const listed = allowed.join(", ");
    return (value, path, errors) => {
      if (!allowed.includes(value as string)) {
        errors.push(path + `: ${JSON.stringify(value)} is not one of [` + listed + `]`);
      }
    };
  }
  if ("a" in type) {
    const item = compileType(type.a);
    return (value, path, errors) => {
      if (!Array.isArray(value)) errors.push(path + ": expected array");
      else value.forEach((entry: unknown, i) => item(entry, path + `[${i}]`, errors));
    };
  }
  if ("d" in type) {
    const entryCheck = compileType(type.d);
    return (value, path, errors) => {
      if (!isRecord(value)) errors.push(path + ": expected object");
      // Bracket notation with a JSON-stringified key: a dict key is
      // arbitrary data, and dot-appending one containing "." or "[0]" would
      // make its error path look like a deeper nested field.
      else Object.entries(value).forEach(([k, v]) => entryCheck(v, path + `[${JSON.stringify(k)}]`, errors));
    };
  }
  return compileFields(type.o);
}

// Field kinds the per-object loop checks inline - no call, and no path string
// built unless the check fails (a valid payload builds none at all for its
// scalar fields, the overwhelming majority).
const KIND_STRING = 1;
const KIND_BOOLEAN = 2;
const KIND_NUMBER = 3;
const KIND_OTHER = 0;

interface CompiledField {
  readonly name: string;
  readonly optional: boolean;
  readonly kind: number;
  readonly check: Check | null;
  readonly missing: string;
  readonly suffix: string;
}

/** The checker for one object shape - build it once, call it per payload. */
export function compileFields(fields: WireFields): Check {
  const compiled: CompiledField[] = fields.map(([name, type, optional]) => {
    const kind = type === "s" ? KIND_STRING : type === "b" ? KIND_BOOLEAN : type === "n" ? KIND_NUMBER : KIND_OTHER;
    return {
      name,
      optional: optional === 1,
      kind,
      check: kind === KIND_OTHER ? compileType(type) : null,
      missing: `.${name}: missing required field`,
      suffix: `.${name}: expected ${type === "s" ? "string" : type === "b" ? "boolean" : "number"}`,
    };
  });
  const count = compiled.length;
  const check: Check = (value, path, errors) => {
    if (!isRecord(value)) {
      errors.push(`${path}: expected object`);
      return;
    }
    for (let i = 0; i < count; i++) {
      const field = compiled[i];
      const fieldValue = value[field.name];
      if (fieldValue === undefined || fieldValue === null) {
        if (!field.optional) errors.push(path + field.missing);
        continue;
      }
      switch (field.kind) {
        case KIND_STRING:
          if (typeof fieldValue !== "string") errors.push(path + field.suffix);
          break;
        case KIND_BOOLEAN:
          if (typeof fieldValue !== "boolean") errors.push(path + field.suffix);
          break;
        case KIND_NUMBER:
          if (typeof fieldValue !== "number") errors.push(path + field.suffix);
          break;
        default:
          field.check!(fieldValue, `${path}.${field.name}`, errors);
      }
    }
  };
  if (!fields.some((field) => field.length > 3)) return check;
  // A sparse row - the scene's node rows. The scene store re-validates the
  // WHOLE scene after every patch, so without this each patch re-checked
  // every row on the canvas, though a patch replaces only the rows it
  // touches: a row object that has passed once is unchanged by construction
  // (stored rows are replaced, never edited in place). Remembering the rows
  // that passed makes a patch's validation cost the rows it carries.
  return (value, path, errors) => {
    if (isRecord(value) && validRows.has(value)) return;
    const before = errors.length;
    check(value, path, errors);
    if (errors.length === before && isRecord(value)) validRows.add(value);
  };
}

// Sparse rows that have passed their check - see the end of compileFields.
const validRows = new WeakSet<object>();

/** The `[name, default]` pairs of a sparse row's table (graphlink_wire_schema
 * .py's "SPARSE ROWS"): every field its sender may leave out. */
export function wireDefaults(fields: WireFields): ReadonlyArray<readonly [string, unknown]> {
  return fields.filter((field) => field.length > 3).map((field) => [field[0], field[3]] as const);
}

/** Restore every field a sparse row's sender left out.
 *
 * Only an ABSENT field is restored - a present one, explicit null included,
 * is data and is left for the validator to judge. Containers restore as
 * fresh empty values (every container default is empty - wire_default
 * enforces it), never a shared instance.
 *
 * A row with nothing missing comes back as the SAME object, so re-validating
 * rows that are already whole (the scene store re-checks the whole scene
 * after every patch) allocates nothing and keeps each untouched row's
 * identity. A row that needs restoring is rebuilt with every field in TABLE
 * order, whichever ones its sender happened to include: every restored row of
 * a kind then has one key order - one hidden class to the JS engine - as the
 * full rows the wire used to carry did, so property reads across the app stay
 * monomorphic. A key the table does not know (a newer sender's field) is
 * kept, after the known ones.
 *
 * Rows this function has already produced or found whole are remembered, so
 * the scene store's re-check of every stored row after each patch skips the
 * per-key scan for them. That is sound because stored rows are replaced,
 * never edited in place - and if one ever were, a defaulted field deleted
 * from it would still be caught: those fields are required by the
 * validator that runs right after. */
const wholeRows = new WeakSet<object>();

export function hydrateRow(
  fields: WireFields,
  defaults: ReadonlyArray<readonly [string, unknown]>,
  value: unknown,
): unknown {
  if (!isRecord(value)) return value;
  if (wholeRows.has(value)) return value;
  let whole = true;
  for (const [key] of defaults) {
    if (value[key] === undefined) {
      whole = false;
      break;
    }
  }
  if (whole) {
    wholeRows.add(value);
    return value;
  }
  const hydrated: Record<string, unknown> = {};
  for (const field of fields) {
    const key = field[0];
    const sent = value[key];
    if (sent !== undefined) hydrated[key] = sent;
    else if (field.length > 3) {
      const fallback = field[3];
      hydrated[key] = Array.isArray(fallback) ? [] : isRecord(fallback) ? {} : fallback;
    }
  }
  for (const key of Object.keys(value)) {
    if (!(key in hydrated)) hydrated[key] = value[key];
  }
  wholeRows.add(hydrated);
  return hydrated;
}
