import type { PortValue } from "../../lib/workflow-contract/generated/workflow";
import { authHeaders } from "../../lib/auth/token";
import { downloadBlob } from "../canvas/downloadTextFile";

/** The run journal stores large structured values by reference. File and image
 * values still include their inline asset metadata; they are ordinary PortValues. */
export type ExternalizedValue = {
  kind: "text" | "json" | "table";
  asset_ref: string;
  mime_type: "application/json";
  size_bytes: number;
};
export type StoredPortValue = PortValue | ExternalizedValue;

export function isExternalizedValue(value: StoredPortValue): value is ExternalizedValue {
  return value.kind !== "file" && value.kind !== "image" && "asset_ref" in value;
}

function validReference(value: ExternalizedValue): boolean {
  return typeof value.asset_ref === "string" && /^[0-9a-f]{64}$/.test(value.asset_ref)
    && value.mime_type === "application/json" && Number.isSafeInteger(value.size_bytes) && value.size_bytes > 0;
}

export function externalValueSummary(value: ExternalizedValue): string {
  return validReference(value) ? `Stored separately · ${(value.size_bytes / 1024).toFixed(1)} KiB`
    : "Stored value reference is invalid.";
}

/** Download the original typed JSON envelope, not a lossy display conversion.
 * The run-scoped endpoint checks membership and the asset store checks its hash.
 * Fetch only on user request, using the same capability header as workflowRequest. */
export async function downloadStoredValue(runPath: string, value: ExternalizedValue, signal: AbortSignal): Promise<void> {
  if (!validReference(value)) throw new Error("This stored value reference is invalid.");
  const response = await fetch(runPath + "/assets/" + encodeURIComponent(value.asset_ref), {
    headers: authHeaders(), signal,
  });
  if (!response.ok) {
    throw new Error(response.status === 404 ? "The stored value is missing or corrupt."
      : `The workflow service answered ${response.status}. Try again.`);
  }
  const blob = await response.blob();
  if (blob.size !== value.size_bytes) throw new Error("The stored value has an unexpected size.");
  if (!signal.aborted) downloadBlob(blob, `workflow-${value.kind}-${value.asset_ref.slice(0, 12)}.json`);
}
