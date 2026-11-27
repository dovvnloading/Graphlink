/**
 * Which Help section an entry point asked for (#1133). Help and Keyboard
 * shortcuts open the same dialog; this tiny store tells it where to land.
 * Every request has a fresh sequence number, so asking for the same
 * section twice (after the user wandered elsewhere) still navigates.
 */
export interface HelpSectionRequest {
  /** Section name, or "" for Help's default landing section. */
  section: string;
  sequence: number;
}

let current: HelpSectionRequest = { section: "", sequence: 0 };
const listeners = new Set<() => void>();

export function requestHelpSection(section: string): void {
  current = { section, sequence: current.sequence + 1 };
  for (const listener of listeners) listener();
}

export function subscribeHelpSection(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function getHelpSectionRequest(): HelpSectionRequest {
  return current;
}

/** Open Help on a section ("" = default landing), or move it there if open. */
export function openHelp(overlays: { open: (name: string, tier: "dialog") => void }, section: string): void {
  requestHelpSection(section);
  overlays.open("help", "dialog");
}
