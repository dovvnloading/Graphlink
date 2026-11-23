import type { ConnectionStatus } from "../lib/ws/transport";

/** Exception-only shell status labels; healthy connections render no badge. */
export function connectionBadgeLabel(status: ConnectionStatus): string {
  if (status === "open") return "";
  if (status === "closed") return "Offline";
  if (status === "reconnecting") return "Reconnecting…";
  return "Connecting…";
}
