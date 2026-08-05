/**
 * The connection badge's user-facing text (rendered by App.tsx's topbar).
 *
 * Its own module rather than an export from App.tsx: `react-refresh/
 * only-export-components` correctly flags a component file that also exports
 * a plain function, and the rule's own advice is to move it here. Being
 * separate also means a test can pin these strings without standing up
 * ReactFlow, the overlay provider and a live WsTransport.
 */
import type { ConnectionStatus } from "../lib/ws/transport";

/**
 * ADR-003 stage 3.6: "reconnecting" gets a real sentence rather than the bare
 * status word, because it is the badge's own visible answer to "why didn't my
 * click do anything" - a session WAS in progress, and intents fired right now
 * are being queued or refused rather than sent (see WsTransport.fireIntent).
 *
 * First-ever "connecting" deliberately keeps the bare word: nothing was ever
 * working yet, so there is nothing paused to report. "closed" likewise stays
 * bare, and after this stage's review-fix it means what it says - the
 * transport has given up, no retry is scheduled - rather than being published
 * for the whole backoff wait between attempts (see transport.ts's onclose).
 */
export function connectionBadgeLabel(status: ConnectionStatus): string {
  if (status === "open") return "connected";
  if (status === "reconnecting") return "reconnecting — actions paused";
  return status;
}
