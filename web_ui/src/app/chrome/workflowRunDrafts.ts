import type { WorkflowSpec } from "../../lib/workflow-contract/generated/workflow";
import { draftFor, typedInputs, type DetachedRequest, type InputDrafts } from "./workflowApi";

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Compare JSON values without treating object key order as an edit. */
function sameJson(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((value, index) => sameJson(value, b[index]));
  }
  if (!isRecord(a) || !isRecord(b)) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every(key =>
    Object.prototype.hasOwnProperty.call(b, key) && sameJson(a[key], b[key]));
}

export function parseDetachedRequest(json: string): DetachedRequest {
  let parsed: unknown;
  try { parsed = JSON.parse(json); } catch { throw new Error("The request isn't valid JSON. Check for a missing comma or quote."); }
  if (!isRecord(parsed) || !isRecord(parsed.spec) || !isRecord(parsed.parameters)) {
    throw new Error('The request needs a "spec" and a "parameters" object.');
  }
  return parsed as DetachedRequest;
}

/** Unlike preview, switching editors permits a draft with missing required inputs. */
export function requestFromDrafts(spec: WorkflowSpec, drafts: InputDrafts): DetachedRequest {
  return { spec, parameters: typedInputs(spec.inputs, drafts, { allowMissingRequired: true }) };
}

/** Only return to the form when it can faithfully express the entire edited request. */
export function draftsForRequest(spec: WorkflowSpec, request: DetachedRequest): InputDrafts {
  if (!sameJson(spec, request.spec)) {
    throw new Error("The workflow spec was edited. Keep editing as JSON, or restore the original spec to use the form.");
  }
  try {
    const drafts = Object.fromEntries(spec.inputs.map(port => [port.name, draftFor(port, request.parameters[port.name])]));
    if (sameJson(requestFromDrafts(spec, drafts), request)) return drafts;
  } catch {
    // Malformed or unrepresentable values must stay in the JSON editor.
  }
  throw new Error("The form can't represent every value in this request. Keep editing as JSON, or remove the unsupported values before switching.");
}
