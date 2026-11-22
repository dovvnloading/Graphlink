import type { HTMLAttributes, ReactNode } from "react";
import "./button-group.css";

export type ButtonGroupProps = Omit<HTMLAttributes<HTMLDivElement>, "aria-label"> & {
  label: string;
  children: ReactNode;
};

export function ButtonGroup({ label, children, className, ...rest }: ButtonGroupProps) {
  return (
    <div
      {...rest}
      role="group"
      aria-label={label}
      className={["gl-button-group", className].filter(Boolean).join(" ")}
    >
      {children}
    </div>
  );
}
