import type { ReactNode } from "react";
import { Icon } from "./icons/Icon";
import { Button } from "./Button";
import "./inline-alert.css";

export type AlertVariant = "info" | "success" | "warning" | "error";
export type InlineAlertProps = {
  variant?: AlertVariant;
  title: string;
  body: ReactNode;
  action?: { label: string; onClick: () => void };
  className?: string;
};

export function InlineAlert({ variant = "info", title, body, action, className }: InlineAlertProps) {
  return (
    <div role={variant === "error" ? "alert" : "status"}
      className={["gl-inline-alert", "gl-inline-alert--" + variant, className].filter(Boolean).join(" ")}>
      <Icon name={"status-" + variant as "status-info" | "status-success" | "status-warning" | "status-error"} size="lg" />
      <div className="gl-inline-alert-content">
        <strong className="gl-inline-alert-title">{title}</strong>
        <div className="gl-inline-alert-body">{body}</div>
        {action && <Button size="sm" onClick={action.onClick}>{action.label}</Button>}
      </div>
    </div>
  );
}
