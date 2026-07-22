/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_app_about_payload.py::AppAboutStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface AppAboutState {
  schemaVersion: number;
  revision: number;
  appName: string;
  appVersion: string;
  repositoryUrl: string;
  developerName: string;
  developerWebsiteUrl: string;
  developerGithubUrl: string;
  copyrightText: string;
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

function checkAppAboutState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["appName"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.appName: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.appName` + ": expected string"); }
  }
  {
    const fieldValue = value["appVersion"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.appVersion: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.appVersion` + ": expected string"); }
  }
  {
    const fieldValue = value["repositoryUrl"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.repositoryUrl: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.repositoryUrl` + ": expected string"); }
  }
  {
    const fieldValue = value["developerName"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.developerName: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.developerName` + ": expected string"); }
  }
  {
    const fieldValue = value["developerWebsiteUrl"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.developerWebsiteUrl: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.developerWebsiteUrl` + ": expected string"); }
  }
  {
    const fieldValue = value["developerGithubUrl"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.developerGithubUrl: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.developerGithubUrl` + ": expected string"); }
  }
  {
    const fieldValue = value["copyrightText"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.copyrightText: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.copyrightText` + ": expected string"); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateAppAboutState(value: unknown): ValidationResult<AppAboutState> {
  const errors: string[] = [];
  checkAppAboutState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as AppAboutState }
    : { ok: false, errors };
}
