/**
 * ADR-012 stage 12.3: a tiny, store-agnostic pub-sub for screen-reader
 * announcements (streaming start/finish, run status). Deliberately NOT a
 * method on composerStore or sceneStore - both need to call into it
 * (composerStore for the main assistant reply, sceneStore for per-node runs:
 * code_sandbox/gitlink/builder/harness/etc.), and neither store imports the
 * other, so a shared home outside both is what keeps this a one-line addition
 * at each call site instead of a new cross-store dependency.
 *
 * App.tsx mounts the one `<div aria-live="polite">` that reads this store's
 * current text (see announcer.test.ts / App.tsx's own render for the
 * subscriber). Everything below is plain module-level state, matching the
 * rest of this app's minimal store pattern (composerStore.ts's own
 * useSyncExternalStore-compatible bind/emit shape) rather than pulling in a
 * state library for one string.
 */

let message = "";
let sequence = 0;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

/**
 * Announce `text` to screen readers via the mounted aria-live region.
 *
 * Repeated identical text (e.g. two "Run completed" transitions with no
 * distinct wording between them) would otherwise be a silent no-op for many
 * screen readers, since the live region's own text content doesn't change -
 * `sequence` is appended as a zero-width marker precisely so the DOM text
 * always changes even when the human-readable message doesn't, without that
 * marker being visible or read aloud (zero-width space has no glyph and no
 * pronunciation).
 */
const ZERO_WIDTH_SPACE = "​";

export function announce(text: string): void {
  sequence += 1;
  message = sequence % 2 === 0 ? `${text}${ZERO_WIDTH_SPACE}` : text;
  emit();
}

export function getAnnouncement(): string {
  return message;
}

export function subscribeAnnouncer(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
