/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_app_about_payload.py::AppAboutStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

import { type ValidationResult, type WireFields, compileFields } from "../wireCheck";

export type { ValidationResult };

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

const APP_ABOUT_STATE_FIELDS: WireFields = [
  ["schemaVersion", "n", 0],
  ["revision", "n", 0],
  ["appName", "s", 0],
  ["appVersion", "s", 0],
  ["repositoryUrl", "s", 0],
  ["developerName", "s", 0],
  ["developerWebsiteUrl", "s", 0],
  ["developerGithubUrl", "s", 0],
  ["copyrightText", "s", 0],
  ["minCompatibleSchemaVersion", "n", 1],
];

const checkAppAboutState = compileFields(APP_ABOUT_STATE_FIELDS);

export function validateAppAboutState(value: unknown): ValidationResult<AppAboutState> {
  const errors: string[] = [];
  checkAppAboutState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as AppAboutState }
    : { ok: false, errors };
}
