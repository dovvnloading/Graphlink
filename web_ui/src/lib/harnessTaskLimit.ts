// Keep in sync with backend/api/intents_harness.py's untrusted wire-input bound.
export const MAX_HARNESS_TASK_LENGTH = 20_000;

// Python's len(str) counts Unicode code points, while JS string.length
// counts UTF-16 code units (two for many emoji). Match the backend cap.
export function exceedsHarnessTaskLimit(text: string): boolean {
  return Array.from(text).length > MAX_HARNESS_TASK_LENGTH;
}
