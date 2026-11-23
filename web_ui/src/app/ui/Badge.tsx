import type { ReactNode } from "react";
import { Icon } from "./icons/Icon";
import type { IconName } from "./icons/registry";
import { Spinner } from "./Spinner";
import "./badge.css";

export type BadgeProps = {
  children: ReactNode;
  size?: "2xs" | "xs";
  className?: string;
};

export function Badge({ children, size = "2xs", className }: BadgeProps) {
  return <span className={["gl-badge", "gl-badge--" + size, className].filter(Boolean).join(" ")}>{children}</span>;
}

const statusIcons = {
  "Running": "status-running",
  "Done": "status-success",
  "Failed": "status-error",
  "Cancelled": "status-cancelled",
  "Waiting for approval": "status-approval",
  "Out of date": "status-stale",
  "Skipped": "status-skipped",
} satisfies Record<string, IconName>;

export type StatusWord = keyof typeof statusIcons;
export type StatusPillStatus = StatusWord | Lowercase<StatusWord>;
export type StatusPillProps = { status: StatusPillStatus; className?: string };

export function StatusPill({ status, className }: StatusPillProps) {
  const label = (status.charAt(0).toUpperCase() + status.slice(1)) as StatusWord;
  return (
    <span className={["gl-status-pill", className].filter(Boolean).join(" ")} role="status" aria-label={label}>
      {label === "Running" ? <Spinner size={12} decorative /> : <Icon name={statusIcons[label]} size="sm" />}
      <span>{label}</span>
    </span>
  );
}
