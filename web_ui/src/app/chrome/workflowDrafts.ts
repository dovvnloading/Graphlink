/**
 * Session-scoped, in-memory drafts for the workflow library's run and
 * preset forms (#1153). The library unmounts hidden tabs and remounts a
 * form on navigation, which used to erase what the user typed. Drafts hold
 * only non-secret form values; previews, approval tokens and fingerprints
 * are never stored, so a restored form always needs a fresh preview.
 */
const drafts = new Map<string, unknown>();

export function readWorkflowDraft<T>(key: string): T | undefined {
  return drafts.get(key) as T | undefined;
}

export function writeWorkflowDraft(key: string, value: unknown): void {
  drafts.set(key, value);
}

export function clearWorkflowDraft(key: string): void {
  drafts.delete(key);
}

export function hasWorkflowDraft(key: string): boolean {
  return drafts.has(key);
}

/** Test helper: forget every draft. */
export function clearAllWorkflowDrafts(): void {
  drafts.clear();
}
