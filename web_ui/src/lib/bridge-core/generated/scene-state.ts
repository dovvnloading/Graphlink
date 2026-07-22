/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_scene_payload.py::SceneStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface SceneNodeRow {
  id: string;
  x: number;
  y: number;
  title: string;
  kind: string;
}

export interface SceneEdgeRow {
  id: string;
  source: string;
  target: string;
}

export interface ScenePinRow {
  id: string;
  title: string;
  note: string;
  x: number;
  y: number;
}

export interface SceneState {
  schemaVersion: number;
  revision: number;
  nodes: SceneNodeRow[];
  edges: SceneEdgeRow[];
  pins: ScenePinRow[];
  snapToGrid: boolean;
  dragFactor: number;
  fontFamily: string;
  fontSizePt: number;
  fontColor: string;
  minCompatibleSchemaVersion?: number | null;
}

export type ValidationResult<T> =
  | { ok: true; value: T }
  | { ok: false; errors: string[] };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// Unknown keys are tolerated on purpose. The JSON Schema marks the contract
// additionalProperties:false because Python and the schema must not drift, but
// an incoming payload carrying a field this build has never heard of is the
// normal, expected shape of a NEWER compatible sender - rejecting it here would
// defeat the additive-forward-compatibility the version negotiation exists to
// provide. Missing or wrongly-typed KNOWN fields are still hard errors.

function checkSceneNodeRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["x"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.x: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.x` + ": expected number"); }
  }
  {
    const fieldValue = value["y"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.y: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.y` + ": expected number"); }
  }
  {
    const fieldValue = value["title"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.title: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.title` + ": expected string"); }
  }
  {
    const fieldValue = value["kind"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.kind: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.kind` + ": expected string"); }
  }
}

function checkSceneEdgeRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["source"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.source: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.source` + ": expected string"); }
  }
  {
    const fieldValue = value["target"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.target: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.target` + ": expected string"); }
  }
}

function checkScenePinRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["title"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.title: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.title` + ": expected string"); }
  }
  {
    const fieldValue = value["note"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.note: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.note` + ": expected string"); }
  }
  {
    const fieldValue = value["x"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.x: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.x` + ": expected number"); }
  }
  {
    const fieldValue = value["y"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.y: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.y` + ": expected number"); }
  }
}

function checkSceneState(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["schemaVersion"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.schemaVersion: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.schemaVersion` + ": expected number"); }
  }
  {
    const fieldValue = value["revision"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.revision: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.revision` + ": expected number"); }
  }
  {
    const fieldValue = value["nodes"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.nodes: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.nodes` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkSceneNodeRow(item, `${path}.nodes` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["edges"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.edges: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.edges` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkSceneEdgeRow(item, `${path}.edges` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["pins"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.pins: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.pins` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkScenePinRow(item, `${path}.pins` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["snapToGrid"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.snapToGrid: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.snapToGrid` + ": expected boolean"); }
  }
  {
    const fieldValue = value["dragFactor"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.dragFactor: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.dragFactor` + ": expected number"); }
  }
  {
    const fieldValue = value["fontFamily"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.fontFamily: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.fontFamily` + ": expected string"); }
  }
  {
    const fieldValue = value["fontSizePt"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.fontSizePt: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.fontSizePt` + ": expected number"); }
  }
  {
    const fieldValue = value["fontColor"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.fontColor: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.fontColor` + ": expected string"); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateSceneState(value: unknown): ValidationResult<SceneState> {
  const errors: string[] = [];
  checkSceneState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as SceneState }
    : { ok: false, errors };
}
