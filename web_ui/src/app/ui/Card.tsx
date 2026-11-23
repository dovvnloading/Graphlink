import type { ReactNode } from "react";
import { StatusPill, type StatusPillStatus } from "./Badge";
import "./card.css";

export type CardProps = {
  title?: string;
  status?: StatusPillStatus;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
};

export function Card({ title, status, actions, children, className }: CardProps) {
  return (
    <section className={["gl-card", className].filter(Boolean).join(" ")} aria-label={title}>
      {(title || status || actions) && (
        <header className="gl-card-header">
          {title && <h3 className="gl-card-title">{title}</h3>}
          {status && <StatusPill status={status} />}
          {actions && <div className="gl-card-actions">{actions}</div>}
        </header>
      )}
      <div className="gl-card-body">{children}</div>
    </section>
  );
}
