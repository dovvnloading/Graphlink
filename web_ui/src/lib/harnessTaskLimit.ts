// Keep in sync with backend/api/intents_harness.py's untrusted wire-input bound.
export const MAX_HARNESS_TASK_LENGTH = 20_000;

// Python's len(str) counts Unicode code points, while JS string.length
// counts UTF-16 code units (two for many emoji). Match the backend cap.
export function exceedsHarnessTaskLimit(text: string): boolean {
  // A UTF-16 string cannot have more code points than code units. Avoid
  // scanning the common short case, then stop at the first excess point
  // instead of allocating an array proportional to an oversized paste.
  if (text.length <= MAX_HARNESS_TASK_LENGTH) return false;
  let count = 0;
  for (const _codePoint of text) {
    if (++count > MAX_HARNESS_TASK_LENGTH) return true;
  }
  return false;
}
