import type { ReactNode } from "react";
import { Icon } from "./icons/Icon";
import type { IconName } from "./icons/registry";
import "./empty-state.css";

export type EmptyStateProps = {
  icon: IconName;
  title: string;
  body: string;
  actions?: ReactNode;
  size?: "sm" | "lg";
  className?: string;
};

export function EmptyState({ icon, title, body, actions, size = "sm", className }: EmptyStateProps) {
  return (
    <div className={["gl-empty-state", "gl-empty-state--" + size, className].filter(Boolean).join(" ")}>
      <Icon name={icon} size={size === "lg" ? "3xl" : "2xl"} />
      <h3 className="gl-empty-state-title">{title}</h3>
      <p className="gl-empty-state-body">{body}</p>
      {actions && <div className="gl-empty-state-actions">{actions}</div>}
    </div>
  );
}
